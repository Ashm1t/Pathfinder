#pragma once
#include "config/Config.h"
#include "llm/ILlmAdapter.h"
#include "mcp/McpClient.h"
#include "mcp/FilesystemMcp.h"
#include "memory/AgentMemory.h"
#include "pipeline/DocumentPipeline.h"
#include "pipeline/CaseExtractor.h"
#include "workflow/WorkflowEngine.h"
#include "agent/PanelDataStore.h"
#include <atomic>
#include <thread>
#include <memory>
#include <deque>
#include <mutex>
#include <condition_variable>

namespace Pathfinder {

// Owns all backend components. Starts the background agent thread.
// Writes results into PanelDataStore; UI reads from there.
class AgentLoop {
public:
    explicit AgentLoop(const Config& cfg);
    ~AgentLoop();

    AgentLoop(const AgentLoop&) = delete;
    AgentLoop& operator=(const AgentLoop&) = delete;

    bool start();
    void stop();
    bool is_running() const { return m_running.load(); }

    PanelDataStore& panel_store() { return m_store; }

    // Trigger a workflow by id manually (e.g. from a UI button).
    WorkflowResult run_workflow(const std::string& workflow_id,
                                const std::string& case_id = "");

private:
    void agent_thread();

    // Called every tick: refresh panel data from memory.
    void refresh_panels();

    // Build What's Next from deadlines + case facts.
    std::vector<WhatsNextItem> compute_whats_next();

    // ── Ingestion work queue ──────────────────────────────────────────────
    // The file watcher only *enqueues* jobs; the worker thread does the heavy
    // LLM extraction and workflow dispatch, so change detection never blocks.
    struct IngestJob {
        std::string             path;
        int64_t                 mtime_ms = 0;
        FileChangeEvent::Type   type;
    };
    void enqueue_job(IngestJob job);
    void worker_thread();

    Config                         m_cfg;
    std::unique_ptr<ILlmAdapter>   m_llm;

    // One McpClient per server.
    std::unique_ptr<McpClient>     m_fs_mcp;
    std::unique_ptr<FilesystemMcp> m_fs;

    std::unique_ptr<AgentMemory>     m_memory;
    std::unique_ptr<DocumentPipeline> m_pipeline;
    std::unique_ptr<CaseExtractor>   m_extractor;
    std::unique_ptr<WorkflowEngine>  m_workflow_engine;

    PanelDataStore    m_store;
    std::atomic<bool> m_running{false};
    std::thread       m_thread;     // periodic tick (deadlines, panel refresh)

    // Ingestion worker
    std::thread                 m_worker;
    std::deque<IngestJob>       m_jobs;
    std::mutex                  m_jobs_mx;
    std::condition_variable     m_jobs_cv;
};

}  // namespace Pathfinder
