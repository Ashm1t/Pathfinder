#pragma once
#include <string>
#include <vector>
#include <functional>

namespace Pathfinder {

struct LlmMessage {
    std::string role;     // "system" | "user" | "assistant"
    std::string content;
};

struct LlmRequest {
    std::string              model;
    std::vector<LlmMessage>  messages;
    float temperature   = 0.1f;
    int   max_tokens    = 2048;
    bool  stream        = false;
};

struct LlmResponse {
    bool        success = false;
    std::string content;
    std::string error;
    int         prompt_tokens     = 0;
    int         completion_tokens = 0;
};

// Streaming callback: called with each token chunk. Return false to cancel.
using StreamCallback = std::function<bool(const std::string& token)>;

class ILlmAdapter {
public:
    virtual ~ILlmAdapter() = default;

    // Blocking completion.
    virtual LlmResponse complete(const LlmRequest& req) = 0;

    // Streaming completion. Calls cb for each token.
    virtual LlmResponse complete_stream(const LlmRequest& req,
                                        StreamCallback cb) = 0;

    // True if the backend is reachable.
    virtual bool        is_available() = 0;

    // Human-readable name for logging.
    virtual std::string adapter_name() const = 0;
};

}  // namespace Pathfinder
