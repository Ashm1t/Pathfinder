#pragma once
#include <string>
#include <vector>
#include <nlohmann/json.hpp>

namespace Pathfinder {

struct LlmConfig {
    std::string adapter;       // "ollama" | "openai_compat"
    std::string base_url;      // "http://localhost:11434"
    std::string model;         // "qwen2.5:3b"
    float       temperature    = 0.1f;
    int         max_tokens     = 2048;
    int         context_window = 4096;
    int         timeout_ms     = 60000;
};

struct McpServerConfig {
    std::string id;            // "filesystem" | "office" | "playwright"
    std::string command;       // "npx"
    std::vector<std::string> args;
    bool        enabled = true;
};

struct AgentConfig {
    std::vector<std::string> watched_folders;
    int    poll_interval_ms  = 5000;
    int    max_file_size_mb  = 50;
    std::vector<std::string> supported_extensions; // {".docx", ".pdf", ".txt"}
};

struct MemoryConfig {
    std::string db_path;      // path to SQLite file
    int  fact_ttl_days  = 90; // facts from closed cases evicted after N days
    bool enable_versioning = true;
};

struct Config {
    LlmConfig                   llm;
    std::vector<McpServerConfig> mcp_servers;
    AgentConfig                 agent;
    MemoryConfig                memory;

    static Config load(const std::string& path);
    void          save(const std::string& path) const;

    static Config defaults();
};

}  // namespace Pathfinder
