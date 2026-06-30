"""Core domain model — ported from the C++ CaseFact taxonomy.

A CaseFact is a single grounded, typed assertion about a case, traceable to its
source document. Facts are versioned in memory; old values are archived on change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_date_to_ms(iso: str) -> int:
    """Parse 'YYYY-MM-DD' to Unix epoch ms (UTC midnight). 0 if invalid."""
    if not iso:
        return 0
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(iso.strip()[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return 0
    return int(dt.timestamp() * 1000)


class FactType(str, Enum):
    CASE_TITLE = "CaseTitle"
    FIR_NUMBER = "FirNumber"
    POLICE_STATION = "PoliceStation"
    DISTRICT = "District"
    DATE_OF_INCIDENT = "DateOfIncident"
    DATE_OF_FIR = "DateOfFIR"
    ACCUSED_NAME = "AccusedName"
    ACCUSED_ADDRESS = "AccusedAddress"
    WITNESS_NAME = "WitnessName"
    VICTIM_NAME = "VictimName"
    IPC_SECTION = "IpcSection"          # BNS / IPC section applied
    CHARGESHEET_DEADLINE = "ChargesheetDeadline"
    COURT_DATE = "CourtDate"
    IO_NAME = "IoName"                  # Investigating Officer
    CASE_STATUS = "CaseStatus"
    NOTICE_ISSUED = "NoticeIssued"
    NOTICE_RESPONSE = "NoticeResponse"
    SEIZED_PROPERTY = "SeizedProperty"
    KEY_EVENT = "KeyEvent"             # general chronology event
    WORKFLOW_STEP = "WorkflowStep"

    @classmethod
    def from_str(cls, s: str) -> "FactType":
        try:
            return cls(s)
        except ValueError:
            return cls.KEY_EVENT


@dataclass
class CaseFact:
    case_id: str
    type: FactType
    value: str
    key: str = ""                       # sub-key for multi-valued types
    source_file: str = ""
    source_page: int = 0
    confidence: float = 1.0
    extracted_at: int = field(default_factory=now_ms)
    event_date_ms: int = 0              # real-world date the fact refers to (0 = none)
    id: int = 0                         # DB row id; 0 = not persisted


@dataclass
class CaseRecord:
    case_id: str
    title: str = ""
    fir_number: str = ""
    police_station: str = ""
    status: str = "active"             # active | chargesheeted | closed
    io_name: str = ""
    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)
