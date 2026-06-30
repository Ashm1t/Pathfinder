#include "pipeline/CaseExtractor.h"
#include <nlohmann/json.hpp>
#include <sstream>
#include <chrono>

namespace Pathfinder {

using json = nlohmann::json;

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(
        system_clock::now().time_since_epoch()).count();
}

// Split text into windows of at most |window| chars, preferring to break on a
// newline near the boundary so sentences aren't cut mid-word. Caps the number
// of chunks so a huge document can't blow up inference cost on a small model.
static std::vector<std::string> chunk_text(const std::string& text,
                                           size_t window,
                                           size_t max_chunks) {
    std::vector<std::string> chunks;
    size_t pos = 0;
    while (pos < text.size() && chunks.size() < max_chunks) {
        size_t end = std::min(pos + window, text.size());
        if (end < text.size()) {
            size_t nl = text.rfind('\n', end);
            if (nl != std::string::npos && nl > pos + window / 2)
                end = nl + 1;
        }
        chunks.push_back(text.substr(pos, end - pos));
        pos = end;
    }
    return chunks;
}

CaseExtractor::CaseExtractor(ILlmAdapter& llm) : m_llm(llm) {}

std::string CaseExtractor::build_extraction_prompt() const {
    return R"PROMPT(
You are a structured data extractor for Indian police case documents.
Extract facts from the provided document text and return ONLY a valid JSON array.
Each element must follow this schema:
{
  "fact_type": "<one of: CaseTitle|FirNumber|PoliceStation|District|DateOfIncident|DateOfFIR|AccusedName|AccusedAddress|WitnessName|VictimName|IpcSection|ChargesheetDeadline|CourtDate|IoName|CaseStatus|NoticeIssued|NoticeResponse|SeizedProperty|KeyEvent>",
  "key": "<sub-identifier if multiple of same type, else empty string>",
  "value": "<extracted value, verbatim from document>",
  "event_date": "<the real-world date this fact refers to, in YYYY-MM-DD format, or empty string if none>",
  "source_page": <page number integer or 0 if unknown>,
  "confidence": <float 0.0-1.0>
}

Rules:
- Extract ALL facts present. Do not invent or infer values not in the text.
- For KeyEvent: value is one sentence describing what happened; event_date is when it happened (YYYY-MM-DD).
- For ChargesheetDeadline / CourtDate / DateOfIncident / DateOfFIR: ALWAYS fill event_date in YYYY-MM-DD.
- Convert any Indian date format (DD/MM/YYYY, DD-MM-YYYY, "15 June 2026") to YYYY-MM-DD in event_date, but keep value verbatim.
- For IpcSection/BNS sections: include both section number and description in value.
- If a fact appears multiple times, use the key field to distinguish (e.g. "accused_1", "accused_2").
- Return ONLY the JSON array, no explanation.
)PROMPT";
}

std::vector<CaseFact> CaseExtractor::parse_response(
    const std::string& json_str,
    const std::string& case_id,
    const std::string& source_path) const
{
    std::vector<CaseFact> facts;

    auto start = json_str.find('[');
    auto end   = json_str.rfind(']');
    if (start == std::string::npos || end == std::string::npos || end < start)
        return facts;

    json arr;
    try {
        arr = json::parse(json_str.substr(start, end - start + 1));
    } catch (...) {
        return facts;
    }
    if (!arr.is_array()) return facts;

    for (auto& item : arr) {
        if (!item.is_object()) continue;
        CaseFact f;
        f.case_id      = case_id;
        f.source_file  = source_path;
        f.extracted_at = now_ms();

        f.type        = fact_type_from_str(item.value("fact_type", "KeyEvent"));
        f.key         = item.value("key",         "");
        f.value       = item.value("value",       "");
        f.source_page = item.value("source_page", 0);
        f.confidence  = item.value("confidence",  1.0f);

        std::string iso = item.value("event_date", "");
        f.event_date_ms = iso_date_to_ms(iso);

        if (!f.value.empty())
            facts.push_back(std::move(f));
    }
    return facts;
}

// Merge facts from multiple chunks, de-duplicating on (type,key,value).
static void merge_facts(std::vector<CaseFact>& into,
                        std::vector<CaseFact>&& more) {
    for (auto& f : more) {
        bool dup = false;
        for (auto& e : into) {
            if (e.type == f.type && e.key == f.key && e.value == f.value) {
                dup = true;
                break;
            }
        }
        if (!dup) into.push_back(std::move(f));
    }
}

std::vector<CaseFact> CaseExtractor::extract(
    const std::string& case_id,
    const std::string& source_path,
    const std::string& document_text)
{
    std::vector<CaseFact> all;
    // ~3000-char windows, up to 6 chunks (keeps small models within context).
    for (auto& chunk : chunk_text(document_text, 3000, 6)) {
        LlmRequest req;
        req.temperature = 0.05f;
        req.max_tokens  = 1024;
        req.messages    = {
            {"system", build_extraction_prompt()},
            {"user",   "Extract facts from this document:\n\n" + chunk}
        };
        auto resp = m_llm.complete(req);
        if (!resp.success) continue;
        merge_facts(all, parse_response(resp.content, case_id, source_path));
    }
    return all;
}

std::vector<CaseFact> CaseExtractor::extract_events(
    const std::string& case_id,
    const std::string& source_path,
    const std::string& document_text)
{
    std::vector<CaseFact> all;
    for (auto& chunk : chunk_text(document_text, 3000, 6)) {
        LlmRequest req;
        req.temperature = 0.05f;
        req.max_tokens  = 512;
        req.messages    = {
            {"system",
                "Extract only chronological events from this Indian police case "
                "diary entry. Return a JSON array of objects with fields: "
                "fact_type (always 'KeyEvent'), key (''), value (one sentence: "
                "what happened), event_date (YYYY-MM-DD), source_page (int), "
                "confidence (float). Return ONLY the JSON array."},
            {"user", chunk}
        };
        auto resp = m_llm.complete(req);
        if (!resp.success) continue;
        merge_facts(all, parse_response(resp.content, case_id, source_path));
    }
    return all;
}

std::vector<CaseFact> CaseExtractor::extract_deadlines(
    const std::string& case_id,
    const std::string& source_path,
    const std::string& document_text)
{
    std::vector<CaseFact> all;
    for (auto& chunk : chunk_text(document_text, 2000, 4)) {
        LlmRequest req;
        req.temperature = 0.05f;
        req.max_tokens  = 256;
        req.messages    = {
            {"system",
                "Extract only deadlines and court dates from this document. "
                "fact_type must be 'ChargesheetDeadline' or 'CourtDate'. "
                "value is the date as written; event_date is the same date in "
                "YYYY-MM-DD. Return ONLY a JSON array with fields: fact_type, "
                "key, value, event_date, source_page, confidence."},
            {"user", chunk}
        };
        auto resp = m_llm.complete(req);
        if (!resp.success) continue;
        merge_facts(all, parse_response(resp.content, case_id, source_path));
    }
    return all;
}

}  // namespace Pathfinder
