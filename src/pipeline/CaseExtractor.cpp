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
  "source_page": <page number integer or 0 if unknown>,
  "confidence": <float 0.0-1.0>
}

Rules:
- Extract ALL facts present. Do not invent or infer values not in the text.
- For KeyEvent: value should be a single sentence describing what happened and when.
- For dates: preserve the original format from the document.
- For IpcSection/BNS sections: include both section number and description.
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

    // Find the JSON array in the response (model may include preamble text)
    auto start = json_str.find('[');
    auto end   = json_str.rfind(']');
    if (start == std::string::npos || end == std::string::npos) return facts;

    json arr;
    try {
        arr = json::parse(json_str.substr(start, end - start + 1));
    } catch (...) {
        return facts;
    }

    for (auto& item : arr) {
        CaseFact f;
        f.case_id      = case_id;
        f.source_file  = source_path;
        f.extracted_at = now_ms();

        std::string type_str = item.value("fact_type", "KeyEvent");
        f.type        = fact_type_from_str(type_str);
        f.key         = item.value("key",         "");
        f.value       = item.value("value",       "");
        f.source_page = item.value("source_page", 0);
        f.confidence  = item.value("confidence",  1.0f);

        if (!f.value.empty())
            facts.push_back(f);
    }
    return facts;
}

std::vector<CaseFact> CaseExtractor::extract(
    const std::string& case_id,
    const std::string& source_path,
    const std::string& document_text)
{
    // Chunk if needed — 3B models have limited context
    // For now: truncate to ~3000 chars to stay well within 4k context
    std::string text = document_text;
    if (text.size() > 3000) text = text.substr(0, 3000) + "\n[truncated]";

    LlmRequest req;
    req.model       = "";  // use configured model
    req.temperature = 0.05f;
    req.max_tokens  = 1024;
    req.messages    = {
        {{"role", "system"}, {"content", build_extraction_prompt()}},
        {{"role", "user"},   {"content", "Extract facts from this document:\n\n" + text}}
    };

    auto resp = m_llm.complete(req);
    if (!resp.success) return {};

    return parse_response(resp.content, case_id, source_path);
}

std::vector<CaseFact> CaseExtractor::extract_events(
    const std::string& case_id,
    const std::string& source_path,
    const std::string& document_text)
{
    std::string text = document_text;
    if (text.size() > 3000) text = text.substr(0, 3000);

    LlmRequest req;
    req.temperature = 0.05f;
    req.max_tokens  = 512;
    req.messages    = {
        {{"role", "system"}, {"content",
            "Extract only chronological events from this Indian police case diary entry. "
            "Return a JSON array of objects with fields: "
            "fact_type (always 'KeyEvent'), key (''), value (one sentence: what happened and when), "
            "source_page (int), confidence (float). Return ONLY the JSON array."}},
        {{"role", "user"}, {"content", text}}
    };

    auto resp = m_llm.complete(req);
    if (!resp.success) return {};
    return parse_response(resp.content, case_id, source_path);
}

std::vector<CaseFact> CaseExtractor::extract_deadlines(
    const std::string& case_id,
    const std::string& source_path,
    const std::string& document_text)
{
    std::string text = document_text;
    if (text.size() > 2000) text = text.substr(0, 2000);

    LlmRequest req;
    req.temperature = 0.05f;
    req.max_tokens  = 256;
    req.messages    = {
        {{"role", "system"}, {"content",
            "Extract only deadlines and court dates from this document. "
            "fact_type must be 'ChargesheetDeadline' or 'CourtDate'. "
            "value should be the date in DD/MM/YYYY format. "
            "Return ONLY a JSON array with fields: fact_type, key, value, source_page, confidence."}},
        {{"role", "user"}, {"content", text}}
    };

    auto resp = m_llm.complete(req);
    if (!resp.success) return {};
    return parse_response(resp.content, case_id, source_path);
}

}  // namespace Pathfinder
