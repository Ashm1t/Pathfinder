#include "pipeline/DocumentPipeline.h"
#include "pipeline/CaseExtractor.h"
#include <algorithm>
#include <filesystem>
#include <iostream>

namespace Pathfinder {

namespace fs = std::filesystem;

// Heuristic: does this content look like plain UTF-8 text (not a binary
// container like a .docx ZIP or a .pdf)? Feeding binary to the LLM produces
// hallucinated facts, so we skip until a real text-extraction MCP (Office)
// is wired in.
static bool looks_like_text(const std::string& content) {
    if (content.size() >= 2 && content[0] == 'P' && content[1] == 'K')
        return false;                              // ZIP (docx/xlsx/pptx)
    if (content.rfind("%PDF", 0) == 0)
        return false;                              // PDF
    size_t sample = std::min<size_t>(content.size(), 4096);
    size_t nontext = 0;
    for (size_t i = 0; i < sample; ++i) {
        unsigned char c = static_cast<unsigned char>(content[i]);
        if (c == 0) return false;                  // NUL → definitely binary
        if (c < 9 || (c > 13 && c < 32)) ++nontext;
    }
    return sample == 0 || (double)nontext / sample < 0.10;
}

DocumentPipeline::DocumentPipeline(FilesystemMcp& fsmcp,
                                   ILlmAdapter&   llm,
                                   AgentMemory&   memory,
                                   const AgentConfig& cfg)
    : m_fs(fsmcp), m_llm(llm), m_memory(memory), m_cfg(cfg) {}

std::string DocumentPipeline::infer_case_id(const std::string& path) const {
    // Convention: immediate parent folder = case_id
    // e.g. C:\CaseDiary\FIR_2026_001\diary.docx  →  "FIR_2026_001"
    fs::path p(path);
    return p.parent_path().filename().string();
}

ParsedDocument DocumentPipeline::extract_text(const std::string& path) {
    ParsedDocument doc;
    doc.path    = path;
    doc.case_id = infer_case_id(path);

    // Read raw content via filesystem MCP
    // For .docx/.pdf, the MCP server returns extracted text if supported,
    // otherwise raw bytes — we take what we get and pass to LLM.
    std::string content = m_fs.read_file(path);
    if (content.empty()) {
        doc.error   = "Empty or unreadable file";
        doc.success = false;
        return doc;
    }

    // Size guard
    size_t max_bytes = (size_t)m_cfg.max_file_size_mb * 1024 * 1024;
    if (content.size() > max_bytes) {
        content = content.substr(0, max_bytes);
    }

    // Reject binary containers — we have no text extractor for them yet.
    if (!looks_like_text(content)) {
        doc.error   = "Binary document (needs Office/text-extraction MCP): " + path;
        doc.success = false;
        std::cerr << "[DocumentPipeline] Skipping " << path
                  << " — not plain text. Wire the Office MCP to ingest "
                     ".docx/.pdf.\n";
        return doc;
    }

    doc.text    = std::move(content);
    doc.success = true;
    return doc;
}

bool DocumentPipeline::process_file(const std::string& path, int64_t mtime_ms) {
    // Check extension
    fs::path p(path);
    std::string ext = p.extension().string();
    std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);

    bool supported = false;
    for (auto& e : m_cfg.supported_extensions)
        if (e == ext) { supported = true; break; }
    if (!supported) return false;

    // Skip unchanged files
    if (!m_memory.needs_processing(path, mtime_ms)) return false;

    ParsedDocument doc = extract_text(path);
    if (!doc.success) return false;

    // Ensure case record exists
    auto existing = m_memory.get_case(doc.case_id);
    if (!existing) {
        CaseRecord rec;
        rec.case_id = doc.case_id;
        rec.title   = doc.case_id;  // will be overwritten by extraction
        rec.status  = "active";
        m_memory.upsert_case(rec);
    }

    // Run LLM extraction
    CaseExtractor extractor(m_llm);
    auto facts = extractor.extract(doc.case_id, path, doc.text);

    // Persist facts
    for (auto& f : facts) {
        m_memory.upsert_fact(f);

        // Update case title from extracted fact
        if (f.type == FactType::CaseTitle && existing) {
            CaseRecord rec = *existing;
            rec.title = f.value;
            m_memory.upsert_case(rec);
        }
    }

    m_memory.mark_processed(path, mtime_ms, doc.case_id);
    return true;
}

void DocumentPipeline::process_folder(const std::string& folder_path) {
    auto files = m_fs.find_files(folder_path, m_cfg.supported_extensions);
    for (auto& entry : files)
        process_file(entry.path, entry.modified_ms);
}

}  // namespace Pathfinder
