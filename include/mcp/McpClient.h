#pragma once
#include <string>
#include <functional>

// Base MCP client — speaks JSON-RPC 2.0 over stdio to a locally-hosted
// MCP server process. Specialised by FileserverClient, OfficeClient,
// DatabaseClient, PlaywrightClient.
class McpClient
{
public:
    explicit McpClient(const std::wstring& serverExePath);
    virtual ~McpClient();

    bool Connect();
    void Disconnect();

    // Send a JSON-RPC request, receive response via callback.
    void Call(const std::string& method,
              const std::string& paramsJson,
              std::function<void(const std::string& resultJson)> callback);

protected:
    std::wstring m_serverExePath;
    HANDLE       m_hProcess  = INVALID_HANDLE_VALUE;
    HANDLE       m_hStdinW   = INVALID_HANDLE_VALUE;
    HANDLE       m_hStdoutR  = INVALID_HANDLE_VALUE;
};
