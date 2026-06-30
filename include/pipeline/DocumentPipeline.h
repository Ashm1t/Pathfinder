#pragma once
#include "mcp/FilesystemMcp.h"
#include "memory/AgentMemory.h"
#include "llm/ILlmAdapter.h"
#include "config/Config.h"
#include <string>
#include <vector>

namespace Pathfinder {

struct ParsedDocument {
    std::string path;
    std::string case_id;      // inferred from folder name
    std::string text;         // extracted plain text
    int         page_count = 0;
    bool        success     = false;
    std::string error;
};

// Reads a document (DOCX/PDF/TXT) via the filesystem MCP server,
// extracts plain text, and returns it for downstream extraction.
class DocumentPipeline {
public:
    DocumentPipeline(FilesystemMcp& fs,
                     ILlmAdapter&   llm,
                     AgentMemory&   memory,
                     const AgentConfig& cfg);

    // Process a single file — extract text, run LLM extraction, store facts.
    // Returns true if the file was processed (false if skipped / unchanged).
    bool process_file(const std::string& path, int64_t mtime_ms);

    // Process all files under a folder (non-recursive triggering from watcher).
    void process_folder(const std::string& folder_path);

private:
    // Extract plain text from a document via MCP.
    ParsedDocument extract_text(const std::string& path);

    // Infer case_id from the file's folder path.
    // Convention: the immediate parent folder name is the case_id.
    std::string infer_case_id(const std::string& path) const;

    FilesystemMcp& m_fs;
    ILlmAdapter&   m_llm;
    AgentMemory&   m_memory;
    AgentConfig    m_cfg;
};

}  // namespace Pathfinder
