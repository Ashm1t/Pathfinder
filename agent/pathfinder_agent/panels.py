"""Panel computation — turns memory into the four HUD panels.

Ported from the C++ AgentLoop::refresh_panels / compute_whats_next. Pure
read-side: given AgentMemory, produce plain dicts the IPC layer serializes to
the HUD. No model calls.
"""
from __future__ import annotations

from typing import Dict, List

from .memory import AgentMemory
from .models import FactType, now_ms


def recent_cases(mem: AgentMemory, limit: int = 10) -> List[Dict]:
    out = []
    for c in mem.list_cases("active")[:limit]:
        chrono = mem.get_chronology(c.case_id)
        out.append({
            "case_id": c.case_id,
            "title": c.title,
            "fir_number": c.fir_number,
            "status": c.status,
            "last_event": chrono[-1].value if chrono else "",
            "last_updated_ms": c.updated_at,
        })
    return out


def major_updates(mem: AgentMemory, within_days: int = 14) -> List[Dict]:
    out = []
    for d in mem.get_upcoming_deadlines(within_days):
        if d.type == FactType.CHARGESHEET_DEADLINE:
            sev, title = "urgent", "Chargesheet Deadline"
            body = f"Case {d.case_id} — deadline: {d.value}"
        else:
            sev, title = "warning", "Court Date"
            body = f"Case {d.case_id} — hearing: {d.value}"
        out.append({
            "severity": sev, "case_id": d.case_id, "title": title,
            "body": body, "timestamp_ms": d.event_date_ms,
            "detected_ms": d.extracted_at,
            "source_file": d.source_file, "source_page": d.source_page,
        })
    return out


def chronology(mem: AgentMemory, case_id: str) -> List[Dict]:
    return [{
        "case_id": f.case_id, "event": f.value, "source_file": f.source_file,
        "timestamp_ms": f.event_date_ms or f.extracted_at,
    } for f in mem.get_chronology(case_id)]


def whats_next(mem: AgentMemory) -> List[Dict]:
    items: List[Dict] = []
    rank = 1
    deadlines = mem.get_upcoming_deadlines(7)

    for d in deadlines:
        if d.type != FactType.CHARGESHEET_DEADLINE:
            continue
        items.append({"rank": rank, "case_id": d.case_id,
                      "action": f"File chargesheet for {d.case_id}",
                      "reason": f"Deadline: {d.value} (within 7 days)",
                      "due_ms": d.event_date_ms,
                      "source_file": d.source_file, "source_page": d.source_page})
        rank += 1

    for d in deadlines:
        if d.type != FactType.COURT_DATE:
            continue
        items.append({"rank": rank, "case_id": d.case_id,
                      "action": f"Prepare documents for court — {d.case_id}",
                      "reason": f"Hearing: {d.value}", "due_ms": d.event_date_ms,
                      "source_file": d.source_file, "source_page": d.source_page})
        rank += 1

    stale = now_ms() - 7 * 86400 * 1000
    for c in mem.list_cases("active"):
        if c.updated_at < stale:
            items.append({"rank": rank, "case_id": c.case_id,
                          "action": f"Update case diary — {c.case_id}",
                          "reason": "No diary entry in last 7 days", "due_ms": 0,
                          "source_file": "", "source_page": 0})
            rank += 1
        if len(items) >= 10:
            break
    return items


def all_panels(mem: AgentMemory) -> Dict:
    return {
        "recent_cases": recent_cases(mem),
        "major_updates": major_updates(mem),
        "whats_next": whats_next(mem),
    }
