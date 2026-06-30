#pragma once
#include <string>
#include <functional>
#include <memory>
#include <atomic>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <unordered_map>
#include <nlohmann/json.hpp>
#include <windows.h>

namespace Pathfinder {

using json = nlohmann::json;

struct McpToolResult {
    bool        success = false;
    std::string error;
    json        content;     // parsed result content array
};

// Callback when a server-side notification arrives.
using NotificationHandler = std::function<void(const std::string& method,
                                               const json& params)>;

// JSON-RPC 2.0 client over stdio to a locally-hosted MCP server process.
class McpClient {
public:
    McpClient(const std::string& id,
              const std::string& command,
              const std::vector<std::string>& args);
    ~McpClient();

    McpClient(const McpClient&) = delete;
    McpClient& operator=(const McpClient&) = delete;

    // Start the server process and perform MCP handshake.
    bool connect();
    void disconnect();
    bool is_connected() const { return m_connected.load(); }

    // Synchronous tool call.
    McpToolResult call_tool(const std::string& tool_name,
                            const json& arguments);

    // List available tools on this server.
    json list_tools();

    void set_notification_handler(NotificationHandler h) {
        m_notification_handler = std::move(h);
    }

    const std::string& id() const { return m_id; }

private:
    // Send a JSON-RPC request; block until response arrives (or timeout).
    json send_request(const std::string& method, const json& params);

    // Send a notification (no response expected).
    void send_notification(const std::string& method, const json& params);

    void write_line(const std::string& line);

    // Background thread: reads stdout of server, dispatches responses.
    void reader_thread();

    std::string              m_id;
    std::string              m_command;
    std::vector<std::string> m_args;

    HANDLE m_hProcess  = INVALID_HANDLE_VALUE;
    HANDLE m_hStdinW   = INVALID_HANDLE_VALUE;
    HANDLE m_hStdoutR  = INVALID_HANDLE_VALUE;

    std::atomic<bool>  m_connected{false};
    std::thread        m_reader;
    int                m_next_id{1};

    // Pending requests: id → {promise fulfilled by reader thread}
    struct Pending {
        json                     response;
        bool                     ready = false;
        std::condition_variable  cv;
    };
    std::mutex                             m_pending_mx;
    std::unordered_map<int, Pending>       m_pending;

    std::mutex             m_write_mx;
    NotificationHandler    m_notification_handler;
};

}  // namespace Pathfinder
