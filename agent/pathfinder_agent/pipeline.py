"""DocumentPipeline — file -> text -> LLM facts -> memory.

Ported from the C++ DocumentPipeline. Convention: a document's case_id is its
immediate parent folder name (matching the FIR-folder layout).
"""
from __future__ import annotations

import os

from . import analyzer
from .config import AgentConfig
from .extraction import CaseExtractor, extract_text
from .memory import AgentMemory
from .models import CaseFact, CaseRecord, FactType, normalize_case_status


def infer_case_id(path: str) -> str:
    return os.path.basename(os.path.dirname(os.path.normpath(path)))


class DocumentPipeline:
    def __init__(self, memory: AgentMemory, extractor: CaseExtractor,
                 cfg: AgentConfig):
        self._mem = memory
        self._extractor = extractor
        self._cfg = cfg

    def process_file(self, path: str, mtime_ms: int, use_llm: bool = True) -> bool:
        ext = os.path.splitext(path)[1].lower()
        if ext not in self._cfg.supported_extensions:
            return False
        if not self._mem.needs_processing(path, mtime_ms):
            return False

        text, err = extract_text(path)
        if text is None:
            print(f"[pipeline] skip {path}: {err}")
            # Record mtime so we don't retry a broken file every poll.
            self._mem.mark_processed(path, mtime_ms, infer_case_id(path))
            return False

        case_id = infer_case_id(path)
        if self._mem.get_case(case_id) is None:
            self._mem.upsert_case(CaseRecord(case_id=case_id, title=case_id))

        # Deterministic header facts ALWAYS run — deadlines, witnesses, IO
        # names from conventioned headers work with the LLM down.
        for fact in analyzer.extract_header_facts(case_id, path, text):
            self._mem.upsert_fact(fact)
            self._promote_to_case_record(case_id, fact)

        if use_llm:
            for fact in self._extractor.extract(case_id, path, text):
                self._mem.upsert_fact(fact)
                self._promote_to_case_record(case_id, fact)

        self._mem.mark_processed(path, mtime_ms, case_id)
        return True

    def _promote_to_case_record(self, case_id: str, fact: CaseFact) -> None:
        """Certain fact types also update the case record itself, so panel
        headers and TTL eviction (which keys off status='closed') see them."""
        field = {FactType.CASE_TITLE: "title",
                 FactType.CASE_STATUS: "status",
                 FactType.POLICE_STATION: "police_station",
                 FactType.IO_NAME: "io_name"}.get(fact.type)
        if field is None:
            return
        rec = self._mem.get_case(case_id)
        if rec is None:
            return
        value = normalize_case_status(fact.value) \
            if fact.type == FactType.CASE_STATUS else fact.value
        if getattr(rec, field) != value:
            setattr(rec, field, value)
            self._mem.upsert_case(rec)
