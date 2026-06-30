#include "mcp/FilesystemMcp.h"
#include <algorithm>
#include <chrono>
#include <thread>
#include <filesystem>
#include <system_error>
#include <windows.h>

namespace Pathfinder {

namespace fs = std::filesystem;

// ── mtime helper ─────────────────────────────────────────────────────────────
// Returns last-write time as Unix epoch milliseconds, or 0 on failure.
// Uses Win32 directly to avoid std::filesystem::file_time_type clock-cast
// portability issues across toolchains.
static int64_t file_mtime_ms(const std::wstring& wpath) {
    WIN32_FILE_ATTRIBUTE_DATA data{};
    if (!GetFileAttributesExW(wpath.c_str(), GetFileExInfoStandard, &data))
        return 0;
    ULARGE_INTEGER ull;
    ull.LowPart  = data.ftLastWriteTime.dwLowDateTime;
    ull.HighPart = data.ftLastWriteTime.dwHighDateTime;
    // FILETIME: 100-ns ticks since 1601-01-01. Convert to ms since 1970.
    constexpr uint64_t EPOCH_DIFF = 116444736000000000ULL; // 1601→1970 in 100ns
    if (ull.QuadPart < EPOCH_DIFF) return 0;
    return static_cast<int64_t>((ull.QuadPart - EPOCH_DIFF) / 10000ULL);
}

static std::wstring to_wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    if (!w.empty() && w.back() == L'\0') w.pop_back();
    return w;
}

FilesystemMcp::FilesystemMcp(McpClient& client) : m_client(client) {}

// ── Content I/O — via the filesystem MCP server ──────────────────────────────

std::string FilesystemMcp::read_file(const std::string& path) {
    auto result = m_client.call_tool("read_file", {{"path", path}});
    if (!result.success) return {};

    if (result.content.is_array()) {
        for (auto& c : result.content)
            if (c.value("type", "") == "text")
                return c.value("text", "");
    }
    return {};
}

bool FilesystemMcp::write_file(const std::string& path,
                                const std::string& content) {
    auto result = m_client.call_tool("write_file",
                                     {{"path", path}, {"content", content}});
    return result.success;
}

// ── Enumeration — via the OS (local folders, correct mtimes) ──────────────────
// The standard filesystem MCP exposes no change-notification or reliable
// mtime API, so directory walking and change detection use std::filesystem.
// Reading/writing file *content* still goes through the MCP server above.

std::vector<FileEntry> FilesystemMcp::list_directory(const std::string& path) {
    std::vector<FileEntry> entries;
    std::error_code ec;
    for (auto& de : fs::directory_iterator(path, ec)) {
        if (ec) break;
        FileEntry e;
        e.path         = de.path().string();
        e.name         = de.path().filename().string();
        e.is_directory = de.is_directory(ec);
        if (!e.is_directory) {
            e.size_bytes  = static_cast<int64_t>(de.file_size(ec));
            e.modified_ms = file_mtime_ms(de.path().wstring());
        }
        entries.push_back(std::move(e));
    }
    return entries;
}

std::vector<FileEntry> FilesystemMcp::find_files(
    const std::string& root,
    const std::vector<std::string>& extensions)
{
    std::vector<FileEntry> results;
    std::error_code ec;

    if (!fs::exists(root, ec)) return results;

    for (auto it = fs::recursive_directory_iterator(
                       root, fs::directory_options::skip_permission_denied, ec);
         it != fs::recursive_directory_iterator(); it.increment(ec))
    {
        if (ec) break;
        const auto& de = *it;
        if (!de.is_regular_file(ec)) continue;

        std::string ext = de.path().extension().string();
        std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
        if (std::find(extensions.begin(), extensions.end(), ext) == extensions.end())
            continue;

        FileEntry e;
        e.path        = de.path().string();
        e.name        = de.path().filename().string();
        e.size_bytes  = static_cast<int64_t>(de.file_size(ec));
        e.modified_ms = file_mtime_ms(de.path().wstring());
        results.push_back(std::move(e));
    }
    return results;
}

// ── Watching ─────────────────────────────────────────────────────────────────

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

        for (auto& [path, mtime] : curr) {
            auto it = prev.find(path);
            if (it == prev.end()) {
                cb({FileChangeEvent::Type::Created, path, mtime});
            } else if (it->second != mtime) {
                cb({FileChangeEvent::Type::Modified, path, mtime});
            }
        }
        for (auto& [path, mtime] : prev) {
            if (curr.find(path) == curr.end())
                cb({FileChangeEvent::Type::Deleted, path, 0});
        }

        prev = std::move(curr);
    }
}

std::unordered_map<std::string, int64_t> FilesystemMcp::snapshot(
    const std::vector<std::string>& folders)
{
    std::unordered_map<std::string, int64_t> result;
    for (auto& folder : folders)
        for (auto& e : find_files(folder, {".docx", ".pdf", ".txt", ".doc"}))
            result[e.path] = e.modified_ms;
    return result;
}

}  // namespace Pathfinder
