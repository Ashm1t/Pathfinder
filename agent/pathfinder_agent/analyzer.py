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
                    value=f"Document on record: {entry}",
                    source_file=path, event_date_ms=ev_ms, confidence=0.6))
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
