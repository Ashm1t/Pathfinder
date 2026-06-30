#pragma once
#include "llm/ILlmAdapter.h"
#include "config/Config.h"

namespace Pathfinder {

class OllamaAdapter final : public ILlmAdapter {
public:
    explicit OllamaAdapter(const LlmConfig& cfg);

    LlmResponse complete(const LlmRequest& req) override;
    LlmResponse complete_stream(const LlmRequest& req,
                                StreamCallback cb) override;
    bool        is_available() override;
    std::string adapter_name() const override { return "ollama"; }

private:
    LlmConfig   m_cfg;

    // WinHTTP request. Throws std::runtime_error on transport failure or any
    // HTTP status >= 400 (message includes the status code and response body).
    std::string http_request(const std::wstring& verb,
                             const std::string& path,
                             const std::string& body,
                             int timeout_ms);
};

// Factory — returns the configured adapter; swap here when adding vLLM etc.
std::unique_ptr<ILlmAdapter> make_llm_adapter(const LlmConfig& cfg);

}  // namespace Pathfinder
