"""Document text extraction + LLM fact extraction.

extract_text() pulls plain text from .txt/.md (always) and .docx/.pdf/.xlsx
(if the optional libraries are installed) — this is the fix for the C++
backend's binary-file gap.

CaseExtractor turns that text into typed CaseFacts via the LLM, with chunking
and ISO-date normalization so deadlines/chronology sort correctly.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

from .llm import LlmAdapter
from .models import CaseFact, FactType, iso_date_to_ms, now_ms

# Fact types whose event_date the model MUST fill; when it forgets, we fall
# back to any parseable date inside the verbatim value.
_DATED_TYPES = {FactType.CHARGESHEET_DEADLINE, FactType.COURT_DATE,
                FactType.DATE_OF_INCIDENT, FactType.DATE_OF_FIR}


def _flex_date_to_ms(s: str) -> int:
    """ISO first; fall back to Indian DD.MM.YYYY / DD/MM/YY forms."""
    ms = iso_date_to_ms(s)
    if ms:
        return ms
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s or "")
    if not m:
        return 0
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    from datetime import datetime, timezone
    try:
        return int(datetime(y, mo, d, tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return 0

# Optional extractors — degrade gracefully if not installed.
try:
    import docx  # python-docx
except ImportError:
    docx = None
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def extract_text(path: str) -> Tuple[Optional[str], str]:
    """Return (text, error). text is None on failure."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".txt", ".md", ".csv", ".json"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), ""
        if ext == ".docx":
            if docx is None:
                return None, "python-docx not installed"
            d = docx.Document(path)
            return "\n".join(p.text for p in d.paragraphs), ""
        if ext == ".pdf":
            if PdfReader is None:
                return None, "pypdf not installed"
            reader = PdfReader(path)
            return "\n".join((pg.extract_text() or "") for pg in reader.pages), ""
        return None, f"unsupported extension: {ext}"
    except Exception as e:  # noqa: BLE001 - surface any reader error
        return None, str(e)


_SYSTEM_PROMPT = """\
You are a structured data extractor for Indian police case documents.
Extract facts from the provided document text and return ONLY a valid JSON array.
Each element:
{
  "fact_type": "<CaseTitle|FirNumber|PoliceStation|District|DateOfIncident|DateOfFIR|AccusedName|AccusedAddress|WitnessName|VictimName|IpcSection|ChargesheetDeadline|CourtDate|IoName|CaseStatus|NoticeIssued|NoticeResponse|SeizedProperty|KeyEvent>",
  "key": "<sub-id if multiple of same type, else empty>",
  "value": "<verbatim value from the document>",
  "event_date": "<real-world date as YYYY-MM-DD, or empty>",
  "source_page": <int or 0>,
  "confidence": <0.0-1.0>
}
Rules:
- Do not invent values not present in the text.
- For ChargesheetDeadline / CourtDate / DateOfIncident / DateOfFIR ALWAYS fill event_date (YYYY-MM-DD).
- Convert any Indian date (DD/MM/YYYY, "15 June 2026") to YYYY-MM-DD for event_date; keep value verbatim.
- Return ONLY the JSON array.
"""


def _chunk(text: str, window: int = 3000, max_chunks: int = 6) -> List[str]:
    chunks, pos = [], 0
    while pos < len(text) and len(chunks) < max_chunks:
        end = min(pos + window, len(text))
        if end < len(text):
            nl = text.rfind("\n", pos, end)
            if nl != -1 and nl > pos + window // 2:
                end = nl + 1
        chunks.append(text[pos:end])
        pos = end
    return chunks


class CaseExtractor:
    def __init__(self, llm: LlmAdapter):
        self._llm = llm

    def extract(self, case_id: str, source_path: str, text: str) -> List[CaseFact]:
        out: List[CaseFact] = []
        for chunk in _chunk(text):
            self._merge(out, self._extract_chunk(case_id, source_path, chunk))
        return out

    def _extract_chunk(self, case_id: str, source_path: str,
                       chunk: str) -> List[CaseFact]:
        """One chunk -> facts, with a single self-repair retry when the
        model's output doesn't parse as a JSON array."""
        messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": "Extract facts:\n\n" + chunk}]
        for _attempt in range(2):
            resp = self._llm.chat(messages, temperature=0.05, max_tokens=1024)
            if not resp.ok:
                return []
            facts = self._parse(resp.content, case_id, source_path)
            if facts:
                return facts
            messages = messages + [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content":
                 "That was not a valid JSON array of facts. Return ONLY the "
                 "JSON array, no prose, no code fences."}]
        return []

    @staticmethod
    def _merge(into: List[CaseFact], more: List[CaseFact]) -> None:
        seen = {(f.type, f.key, f.value) for f in into}
        for f in more:
            sig = (f.type, f.key, f.value)
            if sig not in seen:
                into.append(f)
                seen.add(sig)

    @staticmethod
    def _parse(raw: str, case_id: str, source_path: str) -> List[CaseFact]:
        # Small local models love code fences and trailing commas — tolerate
        # both before giving up.
        raw = raw.replace("```json", "").replace("```", "")
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        snippet = raw[start:end + 1]
        try:
            arr = json.loads(snippet)
        except json.JSONDecodeError:
            try:
                arr = json.loads(re.sub(r",\s*([\]}])", r"\1", snippet))
            except json.JSONDecodeError:
                return []
        facts = []
        for item in arr:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            ftype = FactType.from_str(item.get("fact_type", "KeyEvent"))
            event_ms = _flex_date_to_ms(str(item.get("event_date", "")))
            if not event_ms and ftype in _DATED_TYPES:
                event_ms = _flex_date_to_ms(value)
            try:
                confidence = float(item.get("confidence", 1.0) or 1.0)
                source_page = int(item.get("source_page", 0) or 0)
            except (TypeError, ValueError):
                confidence, source_page = 1.0, 0
            facts.append(CaseFact(
                case_id=case_id,
                type=ftype,
                key=str(item.get("key", "")),
                value=value,
                source_file=source_path,
                source_page=source_page,
                confidence=confidence,
                extracted_at=now_ms(),
                event_date_ms=event_ms,
            ))
        return facts
