#pragma once
#include "memory/CaseFact.h"
#include <string>
#include <vector>
#include <mutex>
#include <functional>
#include <chrono>
#include <unordered_map>

namespace Pathfinder {

// ── Panel data structures ────────────────────────────────────────────────────

struct RecentCaseItem {
    std::string case_id;
    std::string title;
    std::string fir_number;
    std::string status;
    std::string last_event;     // latest KeyEvent value
    int64_t     last_updated_ms = 0;
};

struct UpdateItem {
    enum class Severity { Info, Warning, Urgent };
    Severity    severity = Severity::Info;
    std::string case_id;
    std::string title;
    std::string body;
    int64_t     timestamp_ms = 0;
};

struct ChronologyEntry {
    std::string case_id;
    std::string event;
    std::string source_file;
    int64_t     timestamp_ms = 0;
};

struct WhatsNextItem {
    int         rank = 0;
    std::string case_id;
    std::string action;      // human-readable suggested action
    std::string reason;      // why this is ranked here
    int64_t     due_ms  = 0; // 0 = no deadline
};

// ── Store ────────────────────────────────────────────────────────────────────
// Thread-safe. UI reads; AgentLoop writes.
// Notifies registered listeners when any panel data changes.
class PanelDataStore {
public:
    using ChangeCallback = std::function<void()>;

    // ── Writers (called from AgentLoop thread) ────────────────────────────
    void set_recent_cases  (std::vector<RecentCaseItem>  items);
    void set_updates       (std::vector<UpdateItem>       items);
    void set_chronology    (std::string case_id,
                            std::vector<ChronologyEntry>  entries);
    void set_whats_next    (std::vector<WhatsNextItem>    items);

    // Push a single update alert (appends, doesn't replace).
    void push_update(UpdateItem item);

    // ── Readers (called from UI thread) ──────────────────────────────────
    std::vector<RecentCaseItem>  get_recent_cases()  const;
    std::vector<UpdateItem>      get_updates()        const;
    std::vector<ChronologyEntry> get_chronology(const std::string& case_id) const;
    std::vector<WhatsNextItem>   get_whats_next()     const;

    // ── Change notification ───────────────────────────────────────────────
    void on_change(ChangeCallback cb) { m_change_cb = std::move(cb); }

private:
    void notify() const;

    mutable std::mutex           m_mx;
    std::vector<RecentCaseItem>  m_recent_cases;
    std::vector<UpdateItem>      m_updates;
    // case_id → entries
    std::unordered_map<std::string, std::vector<ChronologyEntry>> m_chronology;
    std::vector<WhatsNextItem>   m_whats_next;
    ChangeCallback               m_change_cb;
};

}  // namespace Pathfinder
