"""Deterministic runners for the 3 workflows that don't need an LLM to work.

Idea borrowed from the `ip_letter` reference repo (github.com/saiesh2401/ip_letter):
its real ISP-notice pipeline has no LLM anywhere in the core mechanic — it parses
structured input (IP + timestamp pairs), attributes each IP to an ISP, and fills
each ISP's own required .docx/.xlsx template. Wherever the output is derivable
from known facts, fill a template; don't leave it to LLM prose.

Applied here to the 3 workflows in config/workflows.json that AREN'T blocked by
the read_document/path bug (see sample_data/README.md item 1):

  - chargesheet_check : completeness checklist against the case's own documents
  - notice_draft       : Section 179 BNSS witness notices, one per witness found
  - court_compliance   : compliance report assembled from the case's own diary

Each function reads the real files in sample_data/<case>/, using the SAME
extract_text() the production pipeline uses (pathfinder_agent.extraction) and
the SAME {{placeholder}} templates already defined in config/workflows.json's
generate_doc steps, so the output is recognisably "that workflow", just run
without Ollama. Output lands in sample_data/_generated/.

This is a standalone demonstration, not a change to workflow.py — if the
pattern is wanted as first-class step types (e.g. "checklist", "fanout_notice"),
that's a separate, deliberate change to make.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathfinder_agent.analyzer import filename_date_to_ms          # noqa: E402
from pathfinder_agent.extraction import extract_text               # noqa: E402
from pathfinder_agent.workflow import _substitute                  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "_generated")

with open(os.path.join(HERE, "..", "config", "workflows.json"), encoding="utf-8") as f:
    WORKFLOW_DEFS = {w["id"]: w for w in json.load(f)}


def _case_dir(case_id: str) -> str:
    return os.path.join(HERE, case_id)


def _read_case_files(case_id: str) -> Dict[str, str]:
    """filename -> text, for every loose document directly in the case folder
    (mirrors the analyzer's convention: accused subfolders hold no documents)."""
    out = {}
    d = _case_dir(case_id)
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        text, err = extract_text(path)
        if text is not None:
            out[name] = text
    return out


def _write(name: str, content: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _first_match(pattern: str, text: str, group: int = 1) -> str:
    m = re.search(pattern, text)
    return m.group(group).strip() if m else ""


def _facts_from_fir_copy(text: str) -> Dict[str, str]:
    return {
        "FirNumber": _first_match(r"FIR No\.:\s*([\d\-/]+)", text),
        "PoliceStation": _first_match(r"Police Station:\s*(.+)", text),
        "IoName": _first_match(r"Investigating Officer:\s*(.+?)(?:,|\.)", text),
    }


# ── 1. chargesheet_check — completeness checklist ───────────────────────────
# Each check is (label, pattern-over-concatenated-text, applies_if). A check
# only fires "MISSING" if applicable and the pattern doesn't match; "PENDING"
# is a special case for items explicitly flagged not-yet-received in the text.
_CHECKLIST: List[Tuple[str, str]] = [
    ("FIR copy on record", r"FIRST INFORMATION REPORT"),
    ("Sections applied stated", r"Sections applied:"),
    ("Investigating Officer named", r"Investigating Officer:"),
    ("At least one accused statement recorded", r"(Interrogation Note|Disclosure Statement)"),
    ("Seizure/recovery memo on record", r"(SEIZURE MEMO|RECOVERY MEMO)"),
    ("Witness statement(s) on record", r"WITNESS STATEMENT"),
    ("Chargesheet deadline stated", r"(chargesheet[\s\S]{0,40}must be filed by|Chargesheet Deadline)"),
]


def run_chargesheet_check(case_id: str) -> str:
    files = _read_case_files(case_id)
    blob = "\n".join(files.values())
    fir = next((t for n, t in files.items() if n.startswith("FIR Copy")), "")
    facts = _facts_from_fir_copy(fir)

    lines = [f"CHARGESHEET COMPLETENESS REPORT", f"Case: {case_id}",
             f"FIR No: {facts.get('FirNumber', '?')}  |  PS: {facts.get('PoliceStation', '?')}",
             f"IO: {facts.get('IoName', '?')}", ""]
    missing = []
    for label, pattern in _CHECKLIST:
        ok = re.search(pattern, blob, re.IGNORECASE) is not None
        lines.append(f"[{'x' if ok else ' '}] {label}")
        if not ok:
            missing.append(label)

    # Explicit "awaited/pending" scan — a real, useful finding distinct from
    # "missing entirely": something IS on record but explicitly not final yet.
    pending = re.findall(r"([A-Za-z ]{0,40}?report[A-Za-z ]{0,10})(?:\s+from\s+FSL[^.]*)?\s+is\s+awaited", blob, re.IGNORECASE)
    if pending:
        lines.append("")
        lines.append("PENDING (on record but not yet complete):")
        for p in pending:
            lines.append(f"  - {p.strip()} — awaited")

    lines.append("")
    if missing:
        lines.append(f"RESULT: {len(missing)} item(s) missing — not ready for filing.")
        severity = "urgent"
    elif pending:
        lines.append("RESULT: checklist complete, but pending items must clear before filing.")
        severity = "warning"
    else:
        lines.append("RESULT: complete — ready for chargesheet filing.")
        severity = "info"

    report = "\n".join(lines)
    out_path = _write(f"{case_id} - chargesheet_check.txt", report)
    print(f"[chargesheet_check] {case_id}: {len(missing)} missing, "
          f"{len(pending)} pending -> {out_path}  (notify severity={severity})")
    return report


# ── 2. notice_draft — Section 179 BNSS witness notice, one per witness ──────
_NOTICE_TEMPLATE = """\
NOTICE UNDER SECTION 179 BNSS
(Notice to a witness to appear before a police officer)

To: {{witness_name}}

Case: {{case_id}} | FIR No: {{fir_number}}
Police Station: {{police_station}}

You are hereby required under Section 179 of the Bharatiya Nagarik Suraksha
Sanhita, 2023 to appear before the undersigned Investigating Officer to
render true and full account of what you know concerning the facts and
circumstances of the above case, in which you have been named as a witness.
Failure to attend without lawful excuse is an offence under law.

Investigating Officer: {{io_name}}
Police Station: {{police_station}}

Date: ___________
Signature: ___________
"""

# Primary rule: dedicated "Witness Statement <Name> ..." files carry an
# explicit "Witness: <Name>, <role>." header line — this is a real, general
# convention (any future witness statement uses the same header field), not
# a one-off regex. Secondary rule: FIR-copy narrative mentions of a witness
# who has no dedicated statement file yet (still to be summoned).
_WITNESS_HEADER_RE = re.compile(r"^Witness:\s*([^,]+),", re.MULTILINE)
_WITNESS_NARRATIVE_RE = re.compile(r"passer-by,\s*([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?),\s*witnessed")


def _find_witnesses(files: Dict[str, str]) -> List[str]:
    found: List[str] = []
    for name, text in files.items():
        if name.startswith("Witness Statement"):
            m = _WITNESS_HEADER_RE.search(text)
            if m and m.group(1).strip() not in found:
                found.append(m.group(1).strip())
    fir = next((t for n, t in files.items() if n.startswith("FIR Copy")), "")
    m = _WITNESS_NARRATIVE_RE.search(fir)
    if m and m.group(1).strip() not in found:
        found.append(m.group(1).strip())
    return found


def run_notice_draft(case_id: str) -> List[str]:
    files = _read_case_files(case_id)
    fir = next((t for n, t in files.items() if n.startswith("FIR Copy")), "")
    facts = _facts_from_fir_copy(fir)
    witnesses = _find_witnesses(files)

    if not witnesses:
        print(f"[notice_draft] {case_id}: no witnesses found in documents -- nothing to draft.")
        return []

    written = []
    for name in witnesses:
        notice = _substitute(_NOTICE_TEMPLATE, {
            "witness_name": name,
            "case_id": case_id,
            "fir_number": facts.get("FirNumber", "?"),
            "police_station": facts.get("PoliceStation", "?"),
            "io_name": facts.get("IoName", "?"),
        })
        safe_name = name.replace(" ", "_")
        out_path = _write(f"{case_id} - notice_draft - {safe_name}.txt", notice)
        written.append(out_path)
    print(f"[notice_draft] {case_id}: {len(witnesses)} witness notice(s) drafted "
          f"({', '.join(witnesses)}) -> {OUT_DIR}")
    return written


# ── 3. court_compliance — assembled from the case's own diary ───────────────
_COMPLIANCE_TEMPLATE = WORKFLOW_DEFS["court_compliance"]["steps"][1]["config"]["template"]
_CD_HEADER_RE = re.compile(
    r"CASE DIARY\s*\n+Case:[^\n]*\n+CD No\.\s*[^,]+,\s*dated\s*([\d.]+)\s*\n+"
    r"Recorded by:[^\n]*\n+\n?([\s\S]*)")


def _cd_headline(text: str) -> str:
    m = _CD_HEADER_RE.search(text)
    if not m:
        return ""
    body = " ".join(m.group(2).split())
    # First sentence is the headline; these are short authored paragraphs so
    # this reliably captures the day's key development.
    end = body.find(". ")
    return body[:end + 1] if end != -1 else body


def run_court_compliance(case_id: str) -> str:
    files = _read_case_files(case_id)
    fir = next((t for n, t in files.items() if n.startswith("FIR Copy")), "")
    facts = _facts_from_fir_copy(fir)

    cd_entries = []
    for name, text in files.items():
        if not name.startswith("CD No."):
            continue
        ts = filename_date_to_ms(name)
        headline = _cd_headline(text)
        if ts and headline:
            cd_entries.append((ts, name, headline))
    cd_entries.sort(key=lambda t: t[0])

    if not cd_entries:
        print(f"[court_compliance] {case_id}: no case-diary entries found -- nothing to compile.")
        return ""

    from datetime import datetime, timezone
    lines = [f"Investigation progress in {case_id} to date:", ""]
    for ts, name, headline in cd_entries:
        date_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
        lines.append(f"- {date_str}: {headline}")
    compliance_report = "\n".join(lines)

    report = _substitute(_COMPLIANCE_TEMPLATE, {
        "case_id": case_id,
        "FirNumber": facts.get("FirNumber", "?"),
        "compliance_report": compliance_report,
    })
    out_path = _write(f"{case_id} - court_compliance.txt", report)
    print(f"[court_compliance] {case_id}: {len(cd_entries)} diary entries compiled -> {out_path}")
    return report


if __name__ == "__main__":
    print(f"=== Working runs against {HERE} ===\n")

    for case_id in ("FIR 214-26", "FIR 189-26", "FIR 302-26"):
        run_chargesheet_check(case_id)
    print()

    for case_id in ("FIR 189-26", "FIR 302-26", "FIR 214-26"):
        run_notice_draft(case_id)
    print()

    run_court_compliance("FIR 214-26")

    print(f"\nAll output written to {OUT_DIR}")
