#include "llm/OllamaAdapter.h"
#include <nlohmann/json.hpp>
#include <windows.h>
#include <winhttp.h>
#include <stdexcept>
#include <sstream>

#pragma comment(lib, "winhttp.lib")

namespace Pathfinder {

using json = nlohmann::json;

// ── WinHTTP RAII helpers ─────────────────────────────────────────────────────

struct WinHttpHandle {
    HINTERNET h = nullptr;
    ~WinHttpHandle() { if (h) WinHttpCloseHandle(h); }
    operator HINTERNET() const { return h; }
};

// ── OllamaAdapter ────────────────────────────────────────────────────────────

OllamaAdapter::OllamaAdapter(const LlmConfig& cfg) : m_cfg(cfg) {}

std::string OllamaAdapter::post_json(const std::string& path,
                                     const std::string& body,
                                     int timeout_ms) {
    // Parse base_url into host + port
    std::string url = m_cfg.base_url;
    std::string host;
    int port = 11434;
    bool secure = false;

    auto strip = [&](const std::string& prefix) -> bool {
        if (url.rfind(prefix, 0) == 0) {
            url = url.substr(prefix.size());
            return true;
        }
        return false;
    };
    if (strip("https://")) secure = true;
    else strip("http://");

    auto colon = url.find(':');
    if (colon != std::string::npos) {
        host = url.substr(0, colon);
        port = std::stoi(url.substr(colon + 1));
    } else {
        host = url;
    }

    // Wide-string conversion
    auto to_wide = [](const std::string& s) -> std::wstring {
        if (s.empty()) return {};
        int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
        std::wstring w(n, 0);
        MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
        return w;
    };

    WinHttpHandle session, connect, request;

    session.h = WinHttpOpen(L"Pathfinder/1.0",
                            WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                            WINHTTP_NO_PROXY_NAME,
                            WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session.h)
        throw std::runtime_error("WinHttpOpen failed");

    WinHttpSetTimeouts(session.h, timeout_ms, timeout_ms, timeout_ms, timeout_ms);

    connect.h = WinHttpConnect(session.h, to_wide(host).c_str(),
                               static_cast<INTERNET_PORT>(port), 0);
    if (!connect.h)
        throw std::runtime_error("WinHttpConnect failed");

    DWORD flags = secure ? WINHTTP_FLAG_SECURE : 0;
    request.h = WinHttpOpenRequest(connect.h, L"POST",
                                   to_wide(path).c_str(),
                                   nullptr, WINHTTP_NO_REFERER,
                                   WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!request.h)
        throw std::runtime_error("WinHttpOpenRequest failed");

    std::wstring headers = L"Content-Type: application/json\r\n";
    WinHttpAddRequestHeaders(request.h, headers.c_str(), (DWORD)-1,
                             WINHTTP_ADDREQ_FLAG_ADD);

    if (!WinHttpSendRequest(request.h,
                            WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            (LPVOID)body.c_str(),
                            (DWORD)body.size(),
                            (DWORD)body.size(), 0))
        throw std::runtime_error("WinHttpSendRequest failed");

    if (!WinHttpReceiveResponse(request.h, nullptr))
        throw std::runtime_error("WinHttpReceiveResponse failed");

    std::string result;
    DWORD bytes_available = 0;
    do {
        bytes_available = 0;
        WinHttpQueryDataAvailable(request.h, &bytes_available);
        if (bytes_available == 0) break;

        std::string chunk(bytes_available, '\0');
        DWORD bytes_read = 0;
        WinHttpReadData(request.h, chunk.data(), bytes_available, &bytes_read);
        result.append(chunk, 0, bytes_read);
    } while (bytes_available > 0);

    return result;
}

LlmResponse OllamaAdapter::complete(const LlmRequest& req) {
    LlmResponse resp;

    json messages = json::array();
    for (auto& m : req.messages)
        messages.push_back({{"role", m.role}, {"content", m.content}});

    json body = {
        {"model",    req.model.empty() ? m_cfg.model : req.model},
        {"messages", messages},
        {"stream",   false},
        {"options",  {
            {"temperature", req.temperature},
            {"num_predict", req.max_tokens},
            {"num_ctx",     m_cfg.context_window}
        }}
    };

    try {
        std::string raw = post_json("/api/chat", body.dump(), m_cfg.timeout_ms);
        json r = json::parse(raw);

        resp.content          = r["message"]["content"].get<std::string>();
        resp.prompt_tokens    = r.value("prompt_eval_count", 0);
        resp.completion_tokens = r.value("eval_count", 0);
        resp.success          = true;
    } catch (const std::exception& e) {
        resp.error   = e.what();
        resp.success = false;
    }
    return resp;
}

LlmResponse OllamaAdapter::complete_stream(const LlmRequest& req,
                                            StreamCallback cb) {
    // Streaming via Ollama sends one JSON object per line with "done": false/true.
    // For simplicity in v1, fall back to blocking complete.
    // TODO: replace with chunked WinHTTP read for true streaming.
    (void)cb;
    return complete(req);
}

bool OllamaAdapter::is_available() {
    try {
        post_json("/api/tags", "", 3000);
        return true;
    } catch (...) {
        return false;
    }
}

// ── Factory ──────────────────────────────────────────────────────────────────

std::unique_ptr<ILlmAdapter> make_llm_adapter(const LlmConfig& cfg) {
    // Hot-swap point: add "vllm", "openai_compat", etc. here.
    if (cfg.adapter == "ollama" || cfg.adapter.empty())
        return std::make_unique<OllamaAdapter>(cfg);

    throw std::runtime_error("Unknown LLM adapter: " + cfg.adapter);
}

}  // namespace Pathfinder
