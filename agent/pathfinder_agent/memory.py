"""AgentMemory — SQLite-backed, versioned knowledge store.

Ported from the C++ AgentMemory: same tables (cases, facts, fact_history,
file_index, watched_folders), value-history versioning, file-change index, and
event-date-aware deadline/chronology queries. Thread-safe via a single lock.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import List, Optional

from typing import Dict

from .models import CaseFact, CaseRecord, FactType, MULTI_VALUED_FACTS, now_ms

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    case_id        TEXT PRIMARY KEY,
    title          TEXT,
    fir_number     TEXT,
    police_station TEXT,
    status         TEXT DEFAULT 'active',
    io_name        TEXT,
    created_at     INTEGER,
    updated_at     INTEGER
);

CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL,
    fact_type     TEXT NOT NULL,
    key           TEXT NOT NULL DEFAULT '',
    value         TEXT NOT NULL,
    source_file   TEXT,
    source_page   INTEGER DEFAULT 0,
    confidence    REAL DEFAULT 1.0,
    extracted_at  INTEGER,
    event_date_ms INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fact_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id    INTEGER NOT NULL,
    old_value  TEXT,
    changed_at INTEGER
);

CREATE TABLE IF NOT EXISTS file_index (
    path           TEXT PRIMARY KEY,
    last_mtime_ms  INTEGER,
    last_processed INTEGER,
    case_id        TEXT
);

CREATE TABLE IF NOT EXISTS watched_folders (
    path    TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message    TEXT NOT NULL,
    severity   TEXT DEFAULT 'info',
    case_id    TEXT DEFAULT '',
    created_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_facts_case  ON facts(case_id);
CREATE INDEX IF NOT EXISTS idx_facts_type  ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_event ON facts(event_date_ms);
"""

_FACT_COLS = ("id, case_id, fact_type, key, value, source_file, "
              "source_page, confidence, extracted_at, event_date_ms")


def _row_to_fact(r: sqlite3.Row) -> CaseFact:
    return CaseFact(
        id=r["id"],
        case_id=r["case_id"],
        type=FactType.from_str(r["fact_type"]),
        key=r["key"],
        value=r["value"],
        source_file=r["source_file"] or "",
        source_page=r["source_page"] or 0,
        confidence=r["confidence"] or 1.0,
        extracted_at=r["extracted_at"] or 0,
        event_date_ms=r["event_date_ms"] or 0,
    )


class AgentMemory:
    def __init__(self, db_path: str):
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── Cases ────────────────────────────────────────────────────────────────
    def upsert_case(self, rec: CaseRecord) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO cases(case_id,title,fir_number,police_station,
                       status,io_name,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(case_id) DO UPDATE SET
                       title=excluded.title, fir_number=excluded.fir_number,
                       police_station=excluded.police_station,
                       status=excluded.status, io_name=excluded.io_name,
                       updated_at=excluded.updated_at""",
                (rec.case_id, rec.title, rec.fir_number, rec.police_station,
                 rec.status, rec.io_name, rec.created_at or now_ms(), now_ms()),
            )
            self._db.commit()

    def get_case(self, case_id: str) -> Optional[CaseRecord]:
        with self._lock:
            r = self._db.execute(
                "SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not r:
            return None
        return CaseRecord(
            case_id=r["case_id"], title=r["title"] or "",
            fir_number=r["fir_number"] or "", police_station=r["police_station"] or "",
            status=r["status"] or "active", io_name=r["io_name"] or "",
            created_at=r["created_at"] or 0, updated_at=r["updated_at"] or 0,
        )

    def list_cases(self, status: str = "") -> List[CaseRecord]:
        with self._lock:
            if status:
                rows = self._db.execute(
                    "SELECT * FROM cases WHERE status=? ORDER BY updated_at DESC",
                    (status,)).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM cases ORDER BY updated_at DESC").fetchall()
        return [CaseRecord(
            case_id=r["case_id"], title=r["title"] or "",
            fir_number=r["fir_number"] or "", police_station=r["police_station"] or "",
            status=r["status"] or "active", io_name=r["io_name"] or "",
            created_at=r["created_at"] or 0, updated_at=r["updated_at"] or 0,
        ) for r in rows]

    # ── Facts (with versioning) ──────────────────────────────────────────────
    def upsert_fact(self, fact: CaseFact) -> None:
        # Identity boundary: (case_id, fact_type, key) is a fact's identity.
        # For multi-valued types an empty key (LLM producers often omit it)
        # would collapse distinct values into one versioned row — derive the
        # key from the value instead so values accumulate.
        if fact.type in MULTI_VALUED_FACTS and not fact.key:
            fact.key = fact.value[:120]
        with self._lock:
            existing = self._db.execute(
                "SELECT id, value FROM facts WHERE case_id=? AND fact_type=? AND key=?",
                (fact.case_id, fact.type.value, fact.key)).fetchone()

            if existing:
                if existing["value"] != fact.value:
                    self._db.execute(
                        "INSERT INTO fact_history(fact_id,old_value,changed_at) "
                        "VALUES(?,?,?)",
                        (existing["id"], existing["value"], now_ms()))
                    self._db.execute(
                        "UPDATE facts SET value=?,source_file=?,source_page=?,"
                        "confidence=?,extracted_at=?,event_date_ms=? WHERE id=?",
                        (fact.value, fact.source_file, fact.source_page,
                         fact.confidence, now_ms(), fact.event_date_ms,
                         existing["id"]))
            else:
                self._db.execute(
                    "INSERT INTO facts(case_id,fact_type,key,value,source_file,"
                    "source_page,confidence,extracted_at,event_date_ms) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (fact.case_id, fact.type.value, fact.key, fact.value,
                     fact.source_file, fact.source_page, fact.confidence,
                     now_ms(), fact.event_date_ms))
            self._db.commit()

    def get_facts(self, case_id: str, ftype: FactType) -> List[CaseFact]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FACT_COLS} FROM facts WHERE case_id=? AND fact_type=?",
                (case_id, ftype.value)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def get_all_facts(self, case_id: str) -> List[CaseFact]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FACT_COLS} FROM facts WHERE case_id=?",
                (case_id,)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def get_chronology(self, case_id: str) -> List[CaseFact]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FACT_COLS} FROM facts "
                "WHERE case_id=? AND fact_type='KeyEvent' "
                "ORDER BY (CASE WHEN event_date_ms>0 THEN event_date_ms "
                "          ELSE extracted_at END) ASC",
                (case_id,)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def get_upcoming_deadlines(self, within_days: int = 14) -> List[CaseFact]:
        now = now_ms()
        limit = now + within_days * 86400 * 1000
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_FACT_COLS} FROM facts "
                "WHERE fact_type IN ('ChargesheetDeadline','CourtDate') "
                "AND event_date_ms BETWEEN ? AND ? ORDER BY event_date_ms ASC",
                (now, limit)).fetchall()
        return [_row_to_fact(r) for r in rows]

    def get_fact_history(self, case_id: str) -> List[Dict]:
        """Audit trail: every archived old value for the case's facts,
        newest change first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT f.fact_type, f.key, f.value AS new_value, "
                "h.old_value, h.changed_at "
                "FROM fact_history h JOIN facts f ON f.id = h.fact_id "
                "WHERE f.case_id=? ORDER BY h.changed_at DESC",
                (case_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── Notifications (persistent — survive restarts) ────────────────────────
    def add_notification(self, payload: Dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO notifications(message,severity,case_id,created_at) "
                "VALUES(?,?,?,?)",
                (payload.get("message", ""), payload.get("severity", "info"),
                 payload.get("case_id", ""), now_ms()))
            # Keep the table bounded.
            self._db.execute(
                "DELETE FROM notifications WHERE id NOT IN "
                "(SELECT id FROM notifications ORDER BY id DESC LIMIT 200)")
            self._db.commit()

    def list_notifications(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT message, severity, case_id, created_at "
                "FROM notifications ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── File index ───────────────────────────────────────────────────────────
    def needs_processing(self, path: str, mtime_ms: int) -> bool:
        with self._lock:
            r = self._db.execute(
                "SELECT last_mtime_ms FROM file_index WHERE path=?",
                (path,)).fetchone()
        return r is None or r["last_mtime_ms"] != mtime_ms

    def mark_processed(self, path: str, mtime_ms: int, case_id: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO file_index(path,last_mtime_ms,last_processed,case_id) "
                "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "last_mtime_ms=excluded.last_mtime_ms,"
                "last_processed=excluded.last_processed,case_id=excluded.case_id",
                (path, mtime_ms, now_ms(), case_id))
            self._db.commit()

    def indexed_paths(self) -> List[str]:
        with self._lock:
            rows = self._db.execute("SELECT path FROM file_index").fetchall()
        return [r["path"] for r in rows]

    def remove_indexed_path(self, path: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM file_index WHERE path=?", (path,))
            self._db.commit()

    # ── Maintenance ──────────────────────────────────────────────────────────
    def evict_old_facts(self, ttl_days: int) -> None:
        cutoff = now_ms() - ttl_days * 86400 * 1000
        with self._lock:
            self._db.execute(
                "DELETE FROM facts WHERE extracted_at < ? AND case_id IN "
                "(SELECT case_id FROM cases WHERE status='closed')", (cutoff,))
            self._db.commit()
