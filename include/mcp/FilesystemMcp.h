#pragma once
#include "mcp/McpClient.h"
#include <functional>
#include <string>
#include <vector>
#include <thread>
#include <atomic>
#include <unordered_map>

namespace Pathfinder {

struct FileEntry {
    std::string path;
    std::string name;
    bool        is_directory = false;
    int64_t     size_bytes   = 0;
    int64_t     modified_ms  = 0;  // Unix ms
};

struct FileChangeEvent {
    enum class Type { Created, Modified, Deleted };
    Type        type;
    std::string path;
    int64_t     mtime_ms = 0;   // last-write time of the file (0 for Deleted)
};

using FileChangeCallback = std::function<void(const FileChangeEvent&)>;

// High-level wrapper around the standard filesystem MCP server
// (@modelcontextprotocol/server-filesystem).
class FilesystemMcp {
public:
    explicit FilesystemMcp(McpClient& client);

    // Read text file contents.
    std::string read_file(const std::string& path);

    // List directory entries.
    std::vector<FileEntry> list_directory(const std::string& path);

    // Recursively list all files matching extensions under root.
    std::vector<FileEntry> find_files(const std::string& root,
                                      const std::vector<std::string>& extensions);

    // Write text content to a file.
    bool write_file(const std::string& path, const std::string& content);

    // Register a callback for file change events (polling-based).
    // Called from the watcher thread — must be thread-safe.
    void start_watching(const std::vector<std::string>& folders,
                        int poll_interval_ms,
                        FileChangeCallback cb);
    void stop_watching();

private:
    void watcher_loop(std::vector<std::string> folders,
                      int poll_ms,
                      FileChangeCallback cb);

    // Returns a map of path → last_modified for all files under folders.
    std::unordered_map<std::string, int64_t> snapshot(
        const std::vector<std::string>& folders);

    McpClient&         m_client;
    std::thread        m_watcher_thread;
    std::atomic<bool>  m_watching{false};
};

}  // namespace Pathfinder
