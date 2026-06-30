#include "mcp/McpClient.h"
#include <sstream>
#include <stdexcept>
#include <iostream>

namespace Pathfinder {

// ── Process helpers ──────────────────────────────────────────────────────────

static std::wstring to_wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    return w;
}

McpClient::McpClient(const std::string& id,
                     const std::string& command,
                     const std::vector<std::string>& args)
    : m_id(id), m_command(command), m_args(args) {}

McpClient::~McpClient() {
    disconnect();
}

bool McpClient::connect() {
    // Build command line: "command arg1 arg2 ..."
    std::string cmdline = m_command;
    for (auto& a : m_args) cmdline += " " + a;

    // Create anonymous pipes for stdin/stdout
    HANDLE hChildStdinR, hChildStdinW;
    HANDLE hChildStdoutR, hChildStdoutW;

    SECURITY_ATTRIBUTES sa{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    if (!CreatePipe(&hChildStdinR,  &hChildStdinW,  &sa, 0)) return false;
    if (!CreatePipe(&hChildStdoutR, &hChildStdoutW, &sa, 0)) {
        CloseHandle(hChildStdinR); CloseHandle(hChildStdinW);
        return false;
    }

    // Don't inherit the write end of stdout or read end of stdin
    SetHandleInformation(hChildStdinW,  HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(hChildStdoutR, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si{};
    si.cb          = sizeof(si);
    si.hStdInput   = hChildStdinR;
    si.hStdOutput  = hChildStdoutW;
    si.hStdError   = GetStdHandle(STD_ERROR_HANDLE);
    si.dwFlags     = STARTF_USESTDHANDLES;

    PROCESS_INFORMATION pi{};
    std::wstring wcmd = to_wide(cmdline);

    bool ok = CreateProcessW(nullptr, wcmd.data(),
                             nullptr, nullptr, TRUE,
                             CREATE_NO_WINDOW, nullptr, nullptr,
                             &si, &pi);

    CloseHandle(hChildStdinR);
    CloseHandle(hChildStdoutW);

    if (!ok) return false;

    CloseHandle(pi.hThread);
    m_hProcess  = pi.hProcess;
    m_hStdinW   = hChildStdinW;
    m_hStdoutR  = hChildStdoutR;
    m_connected = true;

    // Start reader thread before MCP handshake
    m_reader = std::thread(&McpClient::reader_thread, this);

    // MCP handshake: initialize
    json params = {
        {"protocolVersion", "2024-11-05"},
        {"capabilities",    json::object()},
        {"clientInfo",      {{"name", "Pathfinder"}, {"version", "0.1.0"}}}
    };
    json resp = send_request("initialize", params);
    if (resp.contains("error")) {
        disconnect();
        return false;
    }

    // Send initialized notification
    send_notification("notifications/initialized", json::object());
    return true;
}

void McpClient::disconnect() {
    if (!m_connected.exchange(false)) return;

    // Close write end — causes reader thread to see EOF and exit
    if (m_hStdinW != INVALID_HANDLE_VALUE) {
        CloseHandle(m_hStdinW);
        m_hStdinW = INVALID_HANDLE_VALUE;
    }
    if (m_reader.joinable()) m_reader.join();

    if (m_hStdoutR != INVALID_HANDLE_VALUE) {
        CloseHandle(m_hStdoutR);
        m_hStdoutR = INVALID_HANDLE_VALUE;
    }
    if (m_hProcess != INVALID_HANDLE_VALUE) {
        TerminateProcess(m_hProcess, 0);
        WaitForSingleObject(m_hProcess, 2000);
        CloseHandle(m_hProcess);
        m_hProcess = INVALID_HANDLE_VALUE;
    }

    // Wake up any pending waiters
    std::lock_guard<std::mutex> lk(m_pending_mx);
    for (auto& [id, p] : m_pending) {
        p.response = {{"error", "disconnected"}};
        p.ready    = true;
        p.cv.notify_all();
    }
}

void McpClient::write_line(const std::string& line) {
    std::lock_guard<std::mutex> lk(m_write_mx);
    std::string out = line + "\n";
    DWORD written;
    WriteFile(m_hStdinW, out.c_str(), (DWORD)out.size(), &written, nullptr);
}

void McpClient::reader_thread() {
    std::string buffer;
    char chunk[4096];
    DWORD bytes_read;

    while (m_connected) {
        BOOL ok = ReadFile(m_hStdoutR, chunk, sizeof(chunk), &bytes_read, nullptr);
        if (!ok || bytes_read == 0) break;

        buffer.append(chunk, bytes_read);

        // MCP messages are newline-delimited JSON
        size_t pos;
        while ((pos = buffer.find('\n')) != std::string::npos) {
            std::string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            if (line.empty()) continue;

            json msg;
            try { msg = json::parse(line); }
            catch (...) { continue; }

            if (msg.contains("id")) {
                // Response to a request
                int id = msg["id"].get<int>();
                std::lock_guard<std::mutex> lk(m_pending_mx);
                auto it = m_pending.find(id);
                if (it != m_pending.end()) {
                    it->second.response = msg;
                    it->second.ready    = true;
                    it->second.cv.notify_all();
                }
            } else if (msg.contains("method")) {
                // Server-side notification
                if (m_notification_handler)
                    m_notification_handler(msg["method"].get<std::string>(),
                                           msg.value("params", json::object()));
            }
        }
    }
}

json McpClient::send_request(const std::string& method, const json& params) {
    int id;
    {
        std::lock_guard<std::mutex> lk(m_pending_mx);
        id = m_next_id++;
        m_pending[id]; // default-construct Pending
    }

    json req = {
        {"jsonrpc", "2.0"},
        {"id",      id},
        {"method",  method},
        {"params",  params}
    };
    write_line(req.dump());

    // Wait for response
    std::unique_lock<std::mutex> lk(m_pending_mx);
    auto& p = m_pending[id];
    p.cv.wait_for(lk, std::chrono::seconds(30),
                  [&p]{ return p.ready; });

    json resp = p.response;
    m_pending.erase(id);
    return resp;
}

void McpClient::send_notification(const std::string& method, const json& params) {
    json n = {
        {"jsonrpc", "2.0"},
        {"method",  method},
        {"params",  params}
    };
    write_line(n.dump());
}

McpToolResult McpClient::call_tool(const std::string& tool_name,
                                    const json& arguments) {
    json params = {
        {"name",      tool_name},
        {"arguments", arguments}
    };
    json resp = send_request("tools/call", params);

    McpToolResult result;
    if (resp.contains("error")) {
        result.error = resp["error"].dump();
        return result;
    }
    if (resp.contains("result")) {
        result.content = resp["result"];
        result.success = true;
    }
    return result;
}

json McpClient::list_tools() {
    json resp = send_request("tools/list", json::object());
    if (resp.contains("result"))
        return resp["result"]["tools"];
    return json::array();
}

}  // namespace Pathfinder
