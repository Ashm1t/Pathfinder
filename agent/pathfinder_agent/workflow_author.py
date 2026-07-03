"""LLM workflow authoring — officers create workflows on demand.

Turns a natural-language request ("draft a bank freeze letter whenever a new
FIR mentions bank fraud") into a blueprint-conformant workflow definition:

    request -> LLM (given the step catalog + an example) -> JSON
            -> validate_workflow()  -> on errors: one self-repair round,
               feeding the validator's messages back to the model
            -> registered + persisted by the caller (WorkflowEngine.register)

The static validator is the safety boundary: nothing the model writes is
accepted unless its dataflow checks out and it obeys the draft-only rules
(relative output paths, ends with notify). The LLM proposes; the schema
disposes.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Set, Tuple

from .llm import LlmAdapter
from .models import FactType
from .workflow_schema import catalog_text, make_scaffold, validate_workflow

_EXAMPLE = {
    "id": "notice_draft",
    "name": "Witness Notice Generator",
    "description": "Drafts one Section 179 BNSS notice per witness on record",
    "enabled": True,
    "trigger": {"type": "manual", "config": {}},
    "steps": [
        {"type": "gather_case", "name": "Gather case", "config": {}},
        {"type": "for_each", "name": "One notice per witness", "config": {
            "over": "witnesses", "as": "witness",
            "steps": [
                {"type": "generate_doc", "name": "Draft notice", "config": {
                    "output_key": "notice_doc",
                    "template": "NOTICE UNDER SECTION 179 BNSS\n\n"
                                "To: {{witness}}\n"
                                "Case: {{case_id}} | FIR No: {{FirNumber}}\n"
                                "...",
                }},
                {"type": "write_file", "name": "Save draft", "config": {
                    "content_key": "notice_doc",
                    "output_path": "drafts/{{case_id}} - notice - {{witness}}.txt",
                }},
            ],
        }},
        {"type": "notify", "name": "Tell the officer", "config": {
            "message": "{{witness_count}} witness notice draft(s) ready for review",
            "severity": "info",
        }},
    ],
}


def _system_prompt() -> str:
    return f"""\
You author workflow definitions for Pathfinder, a police case-file assistant.
A workflow is a JSON object following a strict blueprint: metadata, one
trigger, and ordered steps that pass data through a shared context.

{catalog_text()}

Hard rules:
- Return ONLY one JSON object. No prose, no code fences.
- Every output is a DRAFT for officer review. write_file paths must be
  relative, under "drafts/". Never write anywhere else. There is no step
  that sends, files, or submits anything — do not invent one.
- The last step must be "notify".
- Use llm_transform ONLY for prose that cannot be produced by filling a
  template from known facts. Prefer generate_doc + fact placeholders.
- NEVER hardcode a file path in read_document's config. Use config.pattern
  (a filename glob such as "FIR Copy*" or "Seizure Memo*") — it resolves
  against the case's own files at run time. Often you don't need
  read_document at all: gather_case + fact placeholders already cover most
  drafting.
- In templates, {{{{key}}}} placeholders may reference ONLY (a) context keys
  provided by the trigger or earlier steps, or (b) one of these stored fact
  types: {", ".join(ft.value for ft in FactType)}.
  Do NOT invent any other placeholder name.
- Indian legal context: BNS (offences), BNSS (procedure — e.g. Section 91
  production of documents, Section 179 witness attendance, Section 193
  chargesheet timeline). Use formal, correct references.

A blank scaffold:
{json.dumps(make_scaffold(), indent=2)}

A complete example:
{json.dumps(_EXAMPLE, indent=2)}
"""


import re as _re

_BAD_MESSAGE_KEY_RE = _re.compile(
    r"placeholder \{\{(\w+)\}\} in 'message'")


def _mechanical_repair(wf: Dict, errors: List[str]) -> Optional[Dict]:
    """Apply fixes the validator prescribes mechanically (no LLM round-trip):
    dead read_document steps, empty for_each steps, and invalid placeholders
    in notify messages (cosmetic — safe to strip). Returns a repaired copy,
    or None if there is nothing mechanical to do."""
    steps = wf.get("steps")
    if not isinstance(steps, list):
        return None
    bad_message_keys = {m.group(1) for e in errors
                        if (m := _BAD_MESSAGE_KEY_RE.search(e))}
    changed = False
    keep = []
    dropped_aliases = []
    for i, s in enumerate(steps):
        if isinstance(s, dict) and s.get("type") == "for_each" \
                and not (s.get("config") or {}).get("steps"):
            changed = True   # for_each without sub-steps does nothing — drop
            dropped_aliases.append((s.get("config") or {}).get("as", "item"))
            continue
        if isinstance(s, dict) and s.get("type") == "read_document":
            rest = json.dumps(steps[i + 1:])
            if "document_text" not in rest and "document_path" not in rest \
                    and '"extract_facts"' not in rest:
                changed = True
                continue  # dead step — validator demands its removal
        if isinstance(s, dict) and s.get("type") == "notify":
            msg = (s.get("config") or {}).get("message", "")
            strip = ["{{case_corpus}}", "{{document_text}}"] + \
                    ["{{" + k + "}}" for k in bad_message_keys]
            for token in strip:
                if token in msg:
                    s = dict(s)
                    s["config"] = dict(s.get("config") or {})
                    s["config"]["message"] = msg = msg.replace(token, "").strip()
                    changed = True
        keep.append(s)
    if not changed:
        return None
    # Scrub references to anything a dropped step would have provided.
    if dropped_aliases:
        scrubbed = []
        for s in keep:
            if isinstance(s, dict) and isinstance(s.get("config"), dict):
                s = dict(s)
                cfg = s["config"] = dict(s["config"])
                for field, val in list(cfg.items()):
                    if isinstance(val, str):
                        for a in dropped_aliases:
                            val = val.replace("{{" + a + "_count}}", "") \
                                     .replace("{{" + a + "}}", "")
                        cfg[field] = val.strip()
            scrubbed.append(s)
        keep = scrubbed
    repaired = dict(wf)
    repaired["steps"] = keep
    return repaired


def _parse_json_object(raw: str) -> Optional[Dict]:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def author_workflow(llm: LlmAdapter, request: str,
                    existing_ids: Set[str]) -> Tuple[Optional[Dict], List[str]]:
    """Author a workflow from a natural-language request.

    Returns (workflow, []) on success or (best_attempt_or_None, errors).
    Performs at most one self-repair round on validation failure.
    """
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": f"Create a workflow for this request:\n\n{request}"},
    ]
    wf: Optional[Dict] = None
    errors: List[str] = ["LLM unavailable"]
    best_errors: List[str] = []

    for _attempt in range(3):
        resp = llm.chat(messages, temperature=0.1, max_tokens=2400)
        if not resp.ok:
            return wf, [f"LLM error: {resp.error}"]
        candidate = _parse_json_object(resp.content)
        if candidate is None:
            # Keep the best parsed candidate; repeat its validation errors so
            # the model keeps fixing the right thing instead of starting over.
            errors = ["response was not a single valid JSON object"] + best_errors
        else:
            wf = candidate
            errors = validate_workflow(candidate, existing_ids=existing_ids)
            best_errors = errors
            if not errors:
                return candidate, []
            repaired = _mechanical_repair(candidate, errors)
            if repaired is not None:
                r_errors = validate_workflow(repaired, existing_ids=existing_ids)
                if not r_errors:
                    return repaired, []
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content":
                         "That definition failed validation. Fix these errors "
                         "and return ONLY the corrected JSON object (no prose, "
                         "no code fences):\n- " + "\n- ".join(errors)})
    return wf, errors
