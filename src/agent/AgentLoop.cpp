#include "agent/AgentLoop.h"
#include "llm/OllamaAdapter.h"   // make_llm_adapter factory
#include <chrono>
#include <iostream>

namespace Pathfinder {

AgentLoop::AgentLoop(const Config& cfg) : m_cfg(cfg) {}

AgentLoop::~AgentLoop() { stop(); }

bool AgentLoop::start() {
    // ── LLM adapter ──────────────────────────────────────────────────────────
    m_llm = make_llm_adapter(m_cfg.llm);
    if (!m_llm->is_available()) {
        std::cerr << "[AgentLoop] LLM not available at " << m_cfg.llm.base_url
                  << " (model: " << m_cfg.llm.model << ")\n";
        return false;
    }

    // ── Memory ────────────────────────────────────────────────────────────────
    m_memory = std::make_unique<AgentMemory>(m_cfg.memory.db_path);
    m_memory->set_watched_folders(m_cfg.agent.watched_folders);

    // ── Filesystem MCP ────────────────────────────────────────────────────────
    const McpServerConfig* fs_cfg = nullptr;
    for (auto& s : m_cfg.mcp_servers)
        if (s.id == "filesystem" && s.enabled) { fs_cfg = &s; break; }

    if (!fs_cfg) {
        std::cerr << "[AgentLoop] No filesystem MCP server configured\n";
        return false;
    }

    m_fs_mcp = std::make_unique<McpClient>(fs_cfg->id,
                                            fs_cfg->command,
                                            fs_cfg->args);
    if (!m_fs_mcp->connect()) {
        std::cerr << "[AgentLoop] Failed to connect to filesystem MCP\n";
        return false;
    }

    m_fs       = std::make_unique<FilesystemMcp>(*m_fs_mcp);
    m_pipeline = std::make_unique<DocumentPipeline>(*m_fs, *m_llm,
                                                     *m_memory, m_cfg.agent);
    m_extractor = std::make_unique<CaseExtractor>(*m_llm);
    m_workflow_engine = std::make_unique<WorkflowEngine>(*m_llm, *m_memory, *m_fs);

    // Load workflow definitions if present
    m_workflow_engine->load_from_file("config/workflows.json");

    // ── Start workers BEFORE watching, so early events are handled ────────────
    m_running = true;
    m_worker  = std::thread(&AgentLoop::worker_thread, this);

    // ── File watcher — only enqueues; the worker does the heavy lifting ───────
    m_fs->start_watching(
        m_cfg.agent.watched_folders,
        m_cfg.agent.poll_interval_ms,
        [this](const FileChangeEvent& ev) {
            if (ev.type == FileChangeEvent::Type::Deleted) return;
            enqueue_job({ev.path, ev.mtime_ms, ev.type});
        }
    );

    // ── Initial scan — enqueue every existing file ────────────────────────────
    for (auto& folder : m_cfg.agent.watched_folders)
        for (auto& f : m_fs->find_files(folder, m_cfg.agent.supported_extensions))
            enqueue_job({f.path, f.modified_ms, FileChangeEvent::Type::Created});

    // ── Background tick thread ────────────────────────────────────────────────
    m_thread = std::thread(&AgentLoop::agent_thread, this);
    return true;
}

void AgentLoop::stop() {
    if (!m_running.exchange(false)) return;
    m_jobs_cv.notify_all();              // wake the worker so it can exit
    if (m_fs) m_fs->stop_watching();
    if (m_worker.joinable()) m_worker.join();
    if (m_thread.joinable()) m_thread.join();
    if (m_fs_mcp) m_fs_mcp->disconnect();
}

void AgentLoop::enqueue_job(IngestJob job) {
    {
        std::lock_guard<std::mutex> lk(m_jobs_mx);
        m_jobs.push_back(std::move(job));
    }
    m_jobs_cv.notify_one();
}

void AgentLoop::worker_thread() {
    while (m_running) {
        IngestJob job;
        {
            std::unique_lock<std::mutex> lk(m_jobs_mx);
            m_jobs_cv.wait(lk, [this] { return !m_jobs.empty() || !m_running; });
            if (!m_running && m_jobs.empty()) break;
            job = std::move(m_jobs.front());
            m_jobs.pop_front();
        }

        // Heavy work: text extraction + LLM + workflow dispatch.
        bool processed = m_pipeline->process_file(job.path, job.mtime_ms);
        m_workflow_engine->dispatch_file_event(job.path, job.type);

        if (processed)
            refresh_panels();
    }
}

void AgentLoop::agent_thread() {
    using namespace std::chrono;
    auto last_deadline_check = steady_clock::now();

    while (m_running) {
        std::this_thread::sleep_for(milliseconds(m_cfg.agent.poll_interval_ms));
        if (!m_running) break;

        // Periodic deadline check (every 30 min)
        auto now = steady_clock::now();
        if (duration_cast<minutes>(now - last_deadline_check).count() >= 30) {
            m_workflow_engine->dispatch_deadline_check();
            m_memory->evict_old_facts(m_cfg.memory.fact_ttl_days);
            last_deadline_check = now;
        }

        refresh_panels();
    }
}

void AgentLoop::refresh_panels() {
    // ── Recent Cases ──────────────────────────────────────────────────────────
    auto cases = m_memory->list_cases("active");
    std::vector<RecentCaseItem> recent;
    for (auto& c : cases) {
        RecentCaseItem item;
        item.case_id     = c.case_id;
        item.title       = c.title;
        item.fir_number  = c.fir_number;
        item.status      = c.status;
        item.last_updated_ms = c.updated_at;

        // Last chronology event as preview
        auto events = m_memory->get_chronology(c.case_id);
        if (!events.empty())
            item.last_event = events.back().value;

        recent.push_back(item);
        if (recent.size() >= 10) break;  // top 10 recent cases
    }
    m_store.set_recent_cases(std::move(recent));

    // ── Major Updates (upcoming deadlines) ────────────────────────────────────
    auto deadlines = m_memory->get_upcoming_deadlines(14);
    std::vector<UpdateItem> updates;
    for (auto& d : deadlines) {
        UpdateItem u;
        u.case_id      = d.case_id;
        u.timestamp_ms = d.extracted_at;

        if (d.type == FactType::ChargesheetDeadline) {
            u.severity = UpdateItem::Severity::Urgent;
            u.title    = "Chargesheet Deadline";
            u.body     = "Case " + d.case_id + " — deadline: " + d.value;
        } else {
            u.severity = UpdateItem::Severity::Warning;
            u.title    = "Court Date";
            u.body     = "Case " + d.case_id + " — hearing: " + d.value;
        }
        updates.push_back(u);
    }
    m_store.set_updates(std::move(updates));

    // ── What's Next ───────────────────────────────────────────────────────────
    m_store.set_whats_next(compute_whats_next());
}

std::vector<WhatsNextItem> AgentLoop::compute_whats_next() {
    std::vector<WhatsNextItem> items;
    int rank = 1;

    // Rank 1: Overdue or imminent chargesheet deadlines
    auto deadlines = m_memory->get_upcoming_deadlines(7);
    for (auto& d : deadlines) {
        if (d.type != FactType::ChargesheetDeadline) continue;
        WhatsNextItem w;
        w.rank    = rank++;
        w.case_id = d.case_id;
        w.action  = "File chargesheet for " + d.case_id;
        w.reason  = "Deadline: " + d.value + " (within 7 days)";
        w.due_ms  = d.extracted_at;
        items.push_back(w);
    }

    // Rank 2: Court dates within 3 days
    for (auto& d : deadlines) {
        if (d.type != FactType::CourtDate) continue;
        WhatsNextItem w;
        w.rank    = rank++;
        w.case_id = d.case_id;
        w.action  = "Prepare documents for court — " + d.case_id;
        w.reason  = "Hearing: " + d.value;
        items.push_back(w);
    }

    // Rank 3: Active cases with no recent diary entry (stale > 7 days)
    auto active = m_memory->list_cases("active");
    int64_t stale_threshold_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count()
        - 7LL * 86400LL * 1000LL;

    for (auto& c : active) {
        if (c.updated_at < stale_threshold_ms) {
            WhatsNextItem w;
            w.rank    = rank++;
            w.case_id = c.case_id;
            w.action  = "Update case diary — " + c.case_id;
            w.reason  = "No diary entry in last 7 days";
            items.push_back(w);
        }
        if (items.size() >= 10) break;
    }

    return items;
}

WorkflowResult AgentLoop::run_workflow(const std::string& workflow_id,
                                        const std::string& case_id) {
    WorkflowContext ctx;
    ctx.case_id = case_id;
    return m_workflow_engine->run(workflow_id, ctx);
}

}  // namespace Pathfinder
