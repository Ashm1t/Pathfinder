#include "config/Config.h"
#include <fstream>
#include <stdexcept>

namespace Pathfinder {

using json = nlohmann::json;

Config Config::defaults() {
    Config c;
    c.llm.adapter        = "ollama";
    c.llm.base_url       = "http://localhost:11434";
    c.llm.model          = "qwen2.5:3b";
    c.llm.temperature    = 0.1f;
    c.llm.max_tokens     = 2048;
    c.llm.context_window = 4096;
    c.llm.timeout_ms     = 60000;

    McpServerConfig fs;
    fs.id      = "filesystem";
    fs.command = "npx";
    fs.args    = {"-y", "@modelcontextprotocol/server-filesystem", "."};
    fs.enabled = true;
    c.mcp_servers.push_back(fs);

    c.agent.poll_interval_ms       = 5000;
    c.agent.max_file_size_mb       = 50;
    c.agent.supported_extensions   = {".docx", ".pdf", ".txt", ".doc"};

    c.memory.db_path           = "pathfinder_memory.db";
    c.memory.fact_ttl_days     = 90;
    c.memory.enable_versioning = true;

    return c;
}

Config Config::load(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open())
        return Config::defaults();

    json j;
    try {
        f >> j;
    } catch (const json::parse_error& e) {
        throw std::runtime_error(std::string("Config parse error: ") + e.what());
    }

    Config c = Config::defaults();

    if (j.contains("llm")) {
        auto& l = j["llm"];
        if (l.contains("adapter"))        c.llm.adapter        = l["adapter"];
        if (l.contains("base_url"))       c.llm.base_url       = l["base_url"];
        if (l.contains("model"))          c.llm.model          = l["model"];
        if (l.contains("temperature"))    c.llm.temperature    = l["temperature"];
        if (l.contains("max_tokens"))     c.llm.max_tokens     = l["max_tokens"];
        if (l.contains("context_window")) c.llm.context_window = l["context_window"];
        if (l.contains("timeout_ms"))     c.llm.timeout_ms     = l["timeout_ms"];
    }

    if (j.contains("mcp_servers")) {
        c.mcp_servers.clear();
        for (auto& s : j["mcp_servers"]) {
            McpServerConfig m;
            m.id      = s.value("id", "");
            m.command = s.value("command", "npx");
            m.enabled = s.value("enabled", true);
            if (s.contains("args"))
                m.args = s["args"].get<std::vector<std::string>>();
            c.mcp_servers.push_back(m);
        }
    }

    if (j.contains("agent")) {
        auto& a = j["agent"];
        if (a.contains("watched_folders"))
            c.agent.watched_folders = a["watched_folders"].get<std::vector<std::string>>();
        if (a.contains("poll_interval_ms"))
            c.agent.poll_interval_ms = a["poll_interval_ms"];
        if (a.contains("max_file_size_mb"))
            c.agent.max_file_size_mb = a["max_file_size_mb"];
        if (a.contains("supported_extensions"))
            c.agent.supported_extensions =
                a["supported_extensions"].get<std::vector<std::string>>();
    }

    if (j.contains("memory")) {
        auto& m = j["memory"];
        if (m.contains("db_path"))           c.memory.db_path           = m["db_path"];
        if (m.contains("fact_ttl_days"))     c.memory.fact_ttl_days     = m["fact_ttl_days"];
        if (m.contains("enable_versioning")) c.memory.enable_versioning = m["enable_versioning"];
    }

    return c;
}

void Config::save(const std::string& path) const {
    json j;
    j["llm"] = {
        {"adapter",        llm.adapter},
        {"base_url",       llm.base_url},
        {"model",          llm.model},
        {"temperature",    llm.temperature},
        {"max_tokens",     llm.max_tokens},
        {"context_window", llm.context_window},
        {"timeout_ms",     llm.timeout_ms}
    };

    json servers = json::array();
    for (auto& s : mcp_servers) {
        servers.push_back({
            {"id",      s.id},
            {"command", s.command},
            {"args",    s.args},
            {"enabled", s.enabled}
        });
    }
    j["mcp_servers"] = servers;

    j["agent"] = {
        {"watched_folders",      agent.watched_folders},
        {"poll_interval_ms",     agent.poll_interval_ms},
        {"max_file_size_mb",     agent.max_file_size_mb},
        {"supported_extensions", agent.supported_extensions}
    };

    j["memory"] = {
        {"db_path",           memory.db_path},
        {"fact_ttl_days",     memory.fact_ttl_days},
        {"enable_versioning", memory.enable_versioning}
    };

    std::ofstream f(path);
    f << j.dump(2);
}

}  // namespace Pathfinder
