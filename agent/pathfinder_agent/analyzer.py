"""Structural analyzer — zero-LLM extraction from the FIR folder convention.

Ported and upgraded from the old backend's analyzer.py. The real-world layout
(confirmed from prior work) is:

    <watched folder>/
        FIR 201-25/                 <- case folder (case_id = "FIR 201-25")
            Ravi Ranjan/            <- subfolder  = accused/suspect
            statement 14.06.25.pdf  <- file; date in name = a chronology event
            chargesheet.docx

From this we can populate, with NO model calls:
  - the CaseRecord (case_id, fir_number)
  - AccusedName facts (one per suspect subfolder)
  - KeyEvent facts dated from filenames

The LLM pass (extraction.py) then fills in what structure can't give us
(sections, deadlines, narrative). Doing the cheap structural pass first
massively cuts LLM load on a small GPU.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from .models import CaseFact, CaseRecord, FactType, now_ms

# DD.MM.YY / DD-MM-YYYY / DD_MM_YY  (separators . - _ )
_DATE_RE = re.compile(r"(\d{1,2})[._-](\d{1,2})[._-](\d{2,4})")
# FIR number like "201-25", "201/25", "FIR 201-25"
_FIR_RE = re.compile(r"(\d{1,4})[\-/](\d{2,4})")


def filename_date_to_ms(name: str) -> Optional[int]:
    """Extract a date from a filename and return Unix ms (UTC midnight)."""
    m = _DATE_RE.search(name)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        dt = datetime(y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def looks_like_case_folder(name: str) -> bool:
    return "FIR" in name.upper()


def analyze_case_folder(folder_path: str) -> List[CaseFact]:
    """Walk one case folder and return structural CaseFacts (no LLM)."""
    case_id = os.path.basename(os.path.normpath(folder_path))
    facts: List[CaseFact] = []

    fir_match = _FIR_RE.search(case_id)
    if fir_match:
        facts.append(CaseFact(
            case_id=case_id, type=FactType.FIR_NUMBER,
            value=fir_match.group(0), source_file=folder_path, confidence=0.9))

    try:
        entries = sorted(os.listdir(folder_path))
    except OSError:
        return facts

    suspect_idx = 0
    for entry in entries:
        path = os.path.join(folder_path, entry)
        if os.path.isdir(path):
            suspect_idx += 1
            facts.append(CaseFact(
                case_id=case_id, type=FactType.ACCUSED_NAME,
                key=f"suspect_{suspect_idx}", value=entry,
                source_file=path, confidence=0.7))
        else:
            ev_ms = filename_date_to_ms(entry)
            if ev_ms is not None:
                facts.append(CaseFact(
                    case_id=case_id, type=FactType.KEY_EVENT,
                    key=entry,  # one chronology row PER document, not one per case
                    value=f"Document on record: {entry}",
                    source_file=path, event_date_ms=ev_ms, confidence=0.6))
    return facts


# ── Header-fact extraction (zero-LLM content pass) ──────────────────────────
# Indian case-file documents carry strongly conventioned header lines
# ("Police Station:", "Witness:", "Investigating Officer:"). Where the
# convention holds, extract deterministically — same lesson as the ip_letter
# reference tool: never spend an LLM call on what a template/regex gives you
# for free. The LLM pass still handles everything narrative.
_HEADER_PATTERNS = [
    # (FactType, regex with one capture group, keyed_by_value)
    (FactType.POLICE_STATION, re.compile(r"^Police Station:\s*(.+?)\s*$", re.M), False),
    (FactType.DISTRICT, re.compile(r"^District:\s*(.+?)\s*$", re.M), False),
    (FactType.IO_NAME, re.compile(r"Investigating Officer:\s*([^,\n.]+)"), False),
    (FactType.VICTIM_NAME, re.compile(r"Complainant / Victim:\s*([^,\n]+),"), False),
    (FactType.WITNESS_NAME, re.compile(r"^Witness:\s*([^,\n]+),", re.M), True),
    (FactType.ACCUSED_NAME, re.compile(r"^Accused:\s*([^,\n]+),", re.M), True),
    (FactType.IPC_SECTION, re.compile(r"Sections applied:\s*([\s\S]+?)\n\n"), False),
    (FactType.CASE_STATUS, re.compile(r"^Case Status:\s*(.+?)\s*$", re.M | re.I), False),
    (FactType.CHARGESHEET_DEADLINE,
     re.compile(r"must be filed by\s*(\d{1,2}\.\d{1,2}\.\d{2,4})"), False),
    (FactType.COURT_DATE,
     re.compile(r"listed for(?: the)? next hearing on\s*(\d{1,2}\.\d{1,2}\.\d{2,4})"),
     False),
]
_DATED_FACTS = {FactType.CHARGESHEET_DEADLINE, FactType.COURT_DATE}


def extract_header_facts(case_id: str, path: str, text: str) -> List[CaseFact]:
    """Deterministic facts from conventioned header lines. No LLM."""
    facts: List[CaseFact] = []
    for ftype, pattern, keyed in _HEADER_PATTERNS:
        for m in pattern.finditer(text):
            value = " ".join(m.group(1).split())
            if not value:
                continue
            ev_ms = 0
            if ftype in _DATED_FACTS:
                ev_ms = filename_date_to_ms(value) or 0
                if not ev_ms:
                    continue  # a dated fact without a parseable date is useless
            facts.append(CaseFact(
                case_id=case_id, type=ftype,
                key=value if keyed else "",
                value=value, source_file=path,
                event_date_ms=ev_ms, confidence=0.8))
            if not keyed:
                break  # single-valued type: first match in the document wins
    return facts


def scan_and_ingest(memory, base_path: str) -> int:
    """Scan a watched folder, upsert case records + structural facts.

    Returns the number of case folders found. Safe to run repeatedly.
    """
    if not os.path.isdir(base_path):
        return 0

    count = 0
    for entry in sorted(os.listdir(base_path)):
        folder = os.path.join(base_path, entry)
        if not (os.path.isdir(folder) and looks_like_case_folder(entry)):
            continue

        count += 1
        case_id = entry
        fir_match = _FIR_RE.search(case_id)
        fir_number = fir_match.group(0) if fir_match else ""

        existing = memory.get_case(case_id)
        rec = existing or CaseRecord(case_id=case_id, title=case_id)
        if fir_number:
            rec.fir_number = fir_number
        memory.upsert_case(rec)

        for fact in analyze_case_folder(folder):
            memory.upsert_fact(fact)

    return count
