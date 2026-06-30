#include "mcp/FilesystemMcp.h"
#include <algorithm>
#include <chrono>
#include <thread>

namespace Pathfinder {

FilesystemMcp::FilesystemMcp(McpClient& client) : m_client(client) {}

std::string FilesystemMcp::read_file(const std::string& path) {
    auto result = m_client.call_tool("read_file", {{"path", path}});
    if (!result.success) return {};

    // MCP content array: [{type:"text", text:"..."}]
    if (result.content.is_array()) {
        for (auto& c : result.content)
            if (c.value("type", "") == "text")
                return c.value("text", "");
    }
    return {};
}

std::vector<FileEntry> FilesystemMcp::list_directory(const std::string& path) {
    auto result = m_client.call_tool("list_directory", {{"path", path}});
    std::vector<FileEntry> entries;
    if (!result.success || !result.content.is_array()) return entries;

    for (auto& c : result.content) {
        if (c.value("type", "") != "text") continue;
        // Standard filesystem MCP returns each entry as "name [FILE|DIR]"
        // Parse the text content into FileEntry structs
        std::string text = c.value("text", "");
        std::istringstream ss(text);
        std::string line;
        while (std::getline(ss, line)) {
            if (line.empty()) continue;
            FileEntry e;
            if (line.find("[DIR]") != std::string::npos) {
                e.is_directory = true;
                e.name = line.substr(0, line.find(" [DIR]"));
            } else if (line.find("[FILE]") != std::string::npos) {
                e.name = line.substr(0, line.find(" [FILE]"));
            } else {
                e.name = line;
            }
            e.path = path + "/" + e.name;
            entries.push_back(e);
        }
    }
    return entries;
}

std::vector<FileEntry> FilesystemMcp::find_files(
    const std::string& root,
    const std::vector<std::string>& extensions)
{
    std::vector<FileEntry> results;
    std::vector<std::string> dirs_to_scan = {root};

    while (!dirs_to_scan.empty()) {
        std::string current = dirs_to_scan.back();
        dirs_to_scan.pop_back();

        for (auto& e : list_directory(current)) {
            if (e.is_directory) {
                dirs_to_scan.push_back(e.path);
            } else {
                // Check extension
                auto dot = e.name.rfind('.');
                if (dot == std::string::npos) continue;
                std::string ext = e.name.substr(dot);
                std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
                if (std::find(extensions.begin(), extensions.end(), ext)
                        != extensions.end())
                    results.push_back(e);
            }
        }
    }
    return results;
}

bool FilesystemMcp::write_file(const std::string& path,
                                const std::string& content) {
    auto result = m_client.call_tool("write_file",
                                     {{"path", path}, {"content", content}});
    return result.success;
}

void FilesystemMcp::start_watching(const std::vector<std::string>& folders,
                                    int poll_interval_ms,
                                    FileChangeCallback cb) {
    m_watching = true;
    m_watcher_thread = std::thread(&FilesystemMcp::watcher_loop,
                                   this, folders, poll_interval_ms, cb);
}

void FilesystemMcp::stop_watching() {
    m_watching = false;
    if (m_watcher_thread.joinable())
        m_watcher_thread.join();
}

void FilesystemMcp::watcher_loop(std::vector<std::string> folders,
                                  int poll_ms,
                                  FileChangeCallback cb) {
    auto prev = snapshot(folders);

    while (m_watching) {
        std::this_thread::sleep_for(std::chrono::milliseconds(poll_ms));
        if (!m_watching) break;

        auto curr = snapshot(folders);

        // Detect changes
        for (auto& [path, mtime] : curr) {
            auto it = prev.find(path);
            if (it == prev.end()) {
                cb({FileChangeEvent::Type::Created, path});
            } else if (it->second != mtime) {
                cb({FileChangeEvent::Type::Modified, path});
            }
        }
        for (auto& [path, mtime] : prev) {
            if (curr.find(path) == curr.end())
                cb({FileChangeEvent::Type::Deleted, path});
        }

        prev = std::move(curr);
    }
}

std::unordered_map<std::string, int64_t> FilesystemMcp::snapshot(
    const std::vector<std::string>& folders)
{
    std::unordered_map<std::string, int64_t> result;
    for (auto& folder : folders) {
        for (auto& e : find_files(folder, {".docx",".pdf",".txt",".doc"})) {
            result[e.path] = e.modified_ms;
        }
    }
    return result;
}

}  // namespace Pathfinder
