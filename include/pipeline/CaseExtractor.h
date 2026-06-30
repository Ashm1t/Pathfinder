#pragma once
#include "llm/ILlmAdapter.h"
#include "memory/CaseFact.h"
#include <string>
#include <vector>

namespace Pathfinder {

// Uses the LLM to extract structured CaseFacts from raw document text.
// All prompts are grounded — extracted values must cite page/section.
// Low temperature (0.05) to minimize hallucination.
class CaseExtractor {
public:
    explicit CaseExtractor(ILlmAdapter& llm);

    // Run full extraction on document text.
    // Returns a list of typed facts ready for AgentMemory::upsert_fact.
    std::vector<CaseFact> extract(const std::string& case_id,
                                  const std::string& source_path,
                                  const std::string& document_text);

    // Extract only chronology events (for incremental diary updates).
    std::vector<CaseFact> extract_events(const std::string& case_id,
                                         const std::string& source_path,
                                         const std::string& document_text);

    // Extract upcoming deadlines only (fast pass, small context).
    std::vector<CaseFact> extract_deadlines(const std::string& case_id,
                                            const std::string& source_path,
                                            const std::string& document_text);

private:
    // Construct the system prompt for structured extraction.
    std::string build_extraction_prompt() const;

    // Parse LLM JSON response into CaseFact list.
    std::vector<CaseFact> parse_response(const std::string& json_str,
                                         const std::string& case_id,
                                         const std::string& source_path) const;

    ILlmAdapter& m_llm;
};

}  // namespace Pathfinder
