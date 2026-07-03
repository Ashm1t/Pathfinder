"""Workflow blueprint — the common structure every workflow is built from.

Extracted from the four hand-written workflows plus the deterministic
sample-data runs: every viable workflow is the same five-phase pipeline —

    TRIGGER  -> GATHER          -> DERIVE            -> PRODUCE      -> DELIVER
    (manual/  (gather_case,      (checklist,          (generate_doc, (write_file
     file/     read_document)     extract_facts,       for_each)      to drafts/,
     deadline)                    llm_transform)                      notify)

— and every output is a DRAFT for officer review; nothing is ever filed or
sent automatically.

This module is the machine-checkable half of that blueprint:

  * STEP_SPECS / TRIGGER_PROVIDES — the typed step catalog: which context
    keys each step needs, which it provides, and its config fields.
  * validate_workflow() — static validation of a workflow definition,
    including a dataflow check (every step's inputs must be satisfied by the
    trigger or an earlier step) and template-placeholder checking. This is
    what makes LLM-authored workflows (workflow_author.py) safe to accept.
  * make_scaffold() — a blank, valid blueprint an officer or LLM starts from.

The human-readable half lives in config/workflow_blueprint.md.
"""
from __future__ import annotations

import difflib
import os
import re
from typing import Dict, List, Set

from .models import FactType

# Context keys each trigger type seeds into the run context.
TRIGGER_PROVIDES: Dict[str, Set[str]] = {
    "manual": {"case_id"},
    "file_created": {"trigger_path"},
    "file_modified": {"trigger_path"},
    "deadline": {"case_id", "deadline_type", "deadline_value"},
}

# The step catalog. For each step type:
#   needs    — context keys that must exist before the step runs
#   provides — context keys the step adds (static ones; output_key handled below)
#   required — config fields that must be present
#   optional — config fields that may be present
#   output_key_default — if set, the step provides ctx[config.output_key or default]
STEP_SPECS: Dict[str, Dict] = {
    "gather_case": {
        "doc": "Load everything known about ctx.case_id from memory + disk: "
               "case files, a concatenated text corpus, chronology, witness/"
               "accused lists. The universal GATHER step.",
        "needs": {"case_id"},
        "provides": {"case_title", "case_files", "case_corpus",
                     "chronology_text", "witnesses", "accused",
                     "seized_property"},
        "required": [], "optional": ["max_corpus_chars"],
    },
    "read_document": {
        "doc": "Load one document's text. Resolution order: config.path -> "
               "ctx.trigger_path -> config.pattern matched against the case's "
               "files (needs case_id).",
        "needs": set(),  # validated specially — see _check_read_document
        "provides": {"document_text", "document_path"},
        "required": [], "optional": ["path", "pattern", "source"],
    },
    "extract_facts": {
        "doc": "LLM fact extraction from ctx.document_text into memory. "
               "Requires the LLM to be reachable.",
        "needs": {"document_text"},
        "provides": {"extracted_fact_count"},
        "required": [], "optional": [],
    },
    "checklist": {
        "doc": "Deterministic completeness scan: regex items over a gathered "
               "text corpus. No LLM. Provides the report plus "
               "checklist_missing (count).",
        "needs": set(),  # source key checked in dataflow pass
        "provides": {"checklist_missing"},
        "required": ["items"], "optional": ["source", "output_key"],
        "output_key_default": "checklist_report",
    },
    "llm_transform": {
        "doc": "One LLM call: system_prompt + user_template (with {{ctx}} "
               "substitution) -> ctx[output_key]. Use ONLY for prose that "
               "cannot be derived deterministically.",
        "needs": set(),
        "provides": set(),
        "required": ["user_template"], "optional": ["system_prompt",
                                                    "temperature", "max_tokens", "output_key"],
        "output_key_default": "llm_output",
    },
    "for_each": {
        "doc": "Fan a list out: run config.steps once per item of "
               "ctx[config.over], with the item bound to ctx[config.as]. "
               "Empty list is a successful no-op.",
        "needs": set(),  # config.over checked in dataflow pass
        "provides": set(),  # provides <as>_count; handled specially
        "required": ["over", "steps"], "optional": ["as"],
    },
    "mcp_call": {
        "doc": "Call a tool on a configured MCP server (the action side — "
               "e.g. filesystem, Office document generation). String argument "
               "values get {{ctx}} substitution. Only servers configured in "
               "agent config are callable.",
        "needs": set(),
        "provides": set(),
        "required": ["server", "tool"], "optional": ["arguments", "output_key"],
        "output_key_default": "mcp_result",
    },
    "generate_doc": {
        "doc": "Fill a text template. {{key}} placeholders resolve from the "
               "run context first, then from the case's stored fact types "
               "(e.g. {{FirNumber}}, {{PoliceStation}}).",
        "needs": set(),
        "provides": set(),
        "required": ["template"], "optional": ["output_key"],
        "output_key_default": "generated_doc",
    },
    "write_file": {
        "doc": "Write ctx[content_key] to a DRAFT file. output_path supports "
               "{{ctx}} substitution and must be a relative path (a drafts "
               "folder) — never an absolute path.",
        "needs": set(),  # content_key checked in dataflow pass
        "provides": {"written_path"},
        "required": ["output_path"], "optional": ["content_key"],
    },
    "notify": {
        "doc": "Push a notification to the HUD (severity: info|warning|urgent). "
               "The standard final step.",
        "needs": set(),
        "provides": {"notification"},
        "required": ["message"], "optional": ["severity"],
    },
}

_TEMPLATE_KEY_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_TEMPLATE_SPAN_RE = re.compile(r"\{\{(.+?)\}\}", re.S)
_FACT_TYPE_NAMES = {ft.value for ft in FactType}
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def _step_provides(step: Dict) -> Set[str]:
    spec = STEP_SPECS.get(step.get("type", ""), {})
    out = set(spec.get("provides", set()))
    if "output_key_default" in spec:
        cfg = step.get("config", {})
        out.add(cfg.get("output_key") or spec["output_key_default"])
    if step.get("type") == "for_each":
        alias = step.get("config", {}).get("as", "item")
        out.add(f"{alias}_count")
    return out


def _template_keys(text: str) -> Set[str]:
    return set(_TEMPLATE_KEY_RE.findall(text or ""))


def _closest_placeholder(key: str, allowed: Set[str]) -> str:
    """Nearest valid placeholder, matching case/underscore-insensitively."""
    norm = key.lower().replace("_", "")
    best, best_score = "", 0.0
    for cand in allowed:
        score = difflib.SequenceMatcher(
            None, norm, cand.lower().replace("_", "")).ratio()
        if score > best_score:
            best, best_score = cand, score
    return best if best_score >= 0.6 else ""


def _check_templates(step: Dict, available: Set[str], errors: List[str],
                     where: str) -> None:
    cfg = step.get("config", {})
    allowed = available | _FACT_TYPE_NAMES
    for field in ("template", "user_template", "output_path", "message"):
        # Placeholders are plain keys — expressions can't execute and would
        # land in the officer's draft as raw text.
        for span in _TEMPLATE_SPAN_RE.findall(cfg.get(field, "") or ""):
            if not re.fullmatch(r"[A-Za-z0-9_]+", span):
                errors.append(
                    f"{where}: '{field}' contains {{{{{span[:40]}}}}} — "
                    f"placeholders are plain keys only, no expressions "
                    f"(write {{{{key}}}}, formatting is automatic)")
        for key in _template_keys(cfg.get(field, "")):
            if key not in allowed:
                close = _closest_placeholder(key, allowed)
                hint = f' — use {{{{{close}}}}} instead' if close else ""
                errors.append(
                    f"{where}: template placeholder {{{{{key}}}}} in "
                    f"'{field}' is not provided by the trigger, any earlier "
                    f"step, or a known fact type{hint}")


def _validate_steps(steps: List, available: Set[str], errors: List[str],
                    prefix: str) -> Set[str]:
    """Dataflow pass. Returns the context keys available after these steps."""
    if not isinstance(steps, list) or not steps:
        errors.append(f"{prefix}: 'steps' must be a non-empty list")
        return available
    for i, step in enumerate(steps):
        where = f"{prefix}.steps[{i}]"
        if not isinstance(step, dict):
            errors.append(f"{where}: step must be an object")
            continue
        stype = step.get("type", "")
        spec = STEP_SPECS.get(stype)
        if spec is None:
            errors.append(f"{where}: unknown step type '{stype}' "
                          f"(known: {', '.join(sorted(STEP_SPECS))})")
            continue
        cfg = step.get("config", {})
        if not isinstance(cfg, dict):
            errors.append(f"{where}: 'config' must be an object")
            cfg = {}
        for req in spec["required"]:
            if req not in cfg:
                errors.append(f"{where} ({stype}): missing required config "
                              f"field '{req}'")
        known_fields = set(spec["required"]) | set(spec["optional"])
        for k in cfg:
            if k not in known_fields and stype != "for_each":
                errors.append(f"{where} ({stype}): unknown config field '{k}'")

        # Step-specific dataflow rules.
        missing = {n for n in spec["needs"] if n not in available}
        if stype == "read_document":
            if not (cfg.get("path") or "trigger_path" in available
                    or (cfg.get("pattern") and "case_id" in available)):
                errors.append(
                    f"{where} (read_document): no way to resolve a document — "
                    f"give config.path, or config.pattern (with case_id in "
                    f"context), or use a file trigger")
            path = cfg.get("path", "")
            if path and not os.path.exists(path):
                errors.append(
                    f"{where} (read_document): config.path '{path}' does not "
                    f"exist — remove 'path' and use config.pattern (a filename "
                    f"glob like \"FIR Copy*\") which resolves against the "
                    f"case's own files at run time")
        elif stype == "checklist":
            src = cfg.get("source", "case_corpus")
            if src not in available:
                errors.append(f"{where} (checklist): source '{src}' is not in "
                              f"context — add a gather_case/read_document step first")
            items = cfg.get("items")
            if isinstance(items, list):
                for j, item in enumerate(items):
                    if not (isinstance(item, dict) and item.get("label")
                            and item.get("pattern")):
                        errors.append(f"{where} (checklist): items[{j}] needs "
                                      f"'label' and 'pattern'")
        elif stype == "write_file":
            ck = cfg.get("content_key", "generated_doc")
            if ck not in available:
                errors.append(f"{where} (write_file): content_key '{ck}' is "
                              f"not in context — generate it first")
            path = cfg.get("output_path", "")
            if path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
                errors.append(f"{where} (write_file): output_path must be "
                              f"relative (a drafts folder), not absolute")
            if ".." in path.replace("\\", "/").split("/"):
                errors.append(f"{where} (write_file): output_path must not "
                              f"contain '..'")
        elif stype == "notify":
            for key in _template_keys(cfg.get("message", "")):
                if key in ("case_corpus", "document_text"):
                    errors.append(
                        f"{where} (notify): {{{{{key}}}}} is bulk document "
                        f"text — notifications must be one short sentence")
        elif stype == "for_each":
            over = cfg.get("over", "")
            if over and over not in available:
                errors.append(f"{where} (for_each): 'over' key '{over}' is "
                              f"not in context — gather it first")
            alias = cfg.get("as", "item")
            sub_available = available | {alias, f"{alias}_index"}
            _validate_steps(cfg.get("steps", []), sub_available, errors, where)
        if missing:
            errors.append(f"{where} ({stype}): needs {sorted(missing)} in "
                          f"context but only {sorted(available)} available")

        _check_templates(step, available if stype != "for_each"
                         else available | {cfg.get("as", "item"),
                                           cfg.get("as", "item") + "_index"},
                         errors, where)
        available = available | _step_provides(step)
    return available


def validate_workflow(wf: Dict, existing_ids: Set[str] = frozenset()) -> List[str]:
    """Return a list of validation errors; empty means the workflow is valid."""
    errors: List[str] = []
    if not isinstance(wf, dict):
        return ["workflow must be a JSON object"]

    wf_id = wf.get("id", "")
    if not _ID_RE.match(str(wf_id)):
        errors.append("'id' must be snake_case, 3-40 chars, starting with a letter")
    elif wf_id in existing_ids:
        errors.append(f"'id' \"{wf_id}\" already exists — pick a new one")
    if not wf.get("name"):
        errors.append("'name' is required")
    if not wf.get("description"):
        errors.append("'description' is required")

    trigger = wf.get("trigger", {})
    ttype = trigger.get("type", "") if isinstance(trigger, dict) else ""
    if ttype not in TRIGGER_PROVIDES:
        errors.append(f"trigger.type must be one of "
                      f"{sorted(TRIGGER_PROVIDES)} (got '{ttype}')")
        available: Set[str] = {"workflow_id"}
    else:
        available = TRIGGER_PROVIDES[ttype] | {"workflow_id"}
        tcfg = trigger.get("config", {})
        if ttype == "deadline" and isinstance(tcfg, dict):
            dt = tcfg.get("type", "")
            if dt and dt not in ("ChargesheetDeadline", "CourtDate"):
                errors.append("deadline trigger 'type' must be "
                              "ChargesheetDeadline or CourtDate")

    _validate_steps(wf.get("steps"), available, errors, wf_id or "workflow")

    # Dataflow lint: a read_document whose output nothing consumes is pure
    # runtime fragility (its file may not resolve) — demand its removal.
    steps = wf.get("steps") or []
    if isinstance(steps, list):
        import json as _json
        for i, step in enumerate(steps):
            if isinstance(step, dict) and step.get("type") == "read_document":
                rest = _json.dumps(steps[i + 1:])
                if "document_text" not in rest and "document_path" not in rest \
                        and '"extract_facts"' not in rest:
                    errors.append(
                        f"steps[{i}] (read_document): its document_text is "
                        f"never used by any later step — remove this step; "
                        f"gather_case + fact placeholders already provide "
                        f"the case data")

    # The blueprint's standing rule: every workflow ends by telling the officer.
    steps = wf.get("steps") or []
    if isinstance(steps, list) and steps and \
            isinstance(steps[-1], dict) and steps[-1].get("type") != "notify":
        errors.append("last step must be 'notify' — every workflow reports "
                      "back to the officer (drafts proposed, never auto-filed)")
    return errors


def make_scaffold(wf_id: str = "my_workflow") -> Dict:
    """A blank, VALID blueprint to edit — the starting point for a new workflow."""
    return {
        "id": wf_id,
        "name": "Human-readable name",
        "description": "One sentence: what this drafts and when it fires",
        "enabled": True,
        "trigger": {"type": "manual", "config": {}},
        "steps": [
            {"type": "gather_case", "name": "Gather case", "config": {}},
            {"type": "generate_doc", "name": "Produce draft", "config": {
                "output_key": "draft",
                "template": "DRAFT for {{case_id}} | FIR No: {{FirNumber}}\n\n"
                            "{{chronology_text}}\n",
            }},
            {"type": "write_file", "name": "Save draft", "config": {
                "content_key": "draft",
                "output_path": "drafts/{{case_id}} - " + wf_id + ".txt",
            }},
            {"type": "notify", "name": "Tell the officer", "config": {
                "message": "Draft ready for review", "severity": "info",
            }},
        ],
    }


def catalog_text() -> str:
    """Render the step catalog as text (used in the LLM authoring prompt)."""
    lines = ["Trigger types and the context they provide:"]
    for t, keys in TRIGGER_PROVIDES.items():
        lines.append(f"  - {t}: provides {sorted(keys)}")
    lines.append("")
    lines.append("Step types:")
    for name, spec in STEP_SPECS.items():
        lines.append(f"  - {name}: {spec['doc']}")
        lines.append(f"      needs: {sorted(spec['needs']) or '-'} | "
                     f"provides: {sorted(_step_provides({'type': name, 'config': {}}))} | "
                     f"config required: {spec['required'] or '-'} | "
                     f"optional: {spec['optional'] or '-'}")
    return "\n".join(lines)
