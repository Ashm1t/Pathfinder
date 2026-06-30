#pragma once
#include "memory/CaseFact.h"
#include <string>
#include <vector>
#include <optional>
#include <memory>
#include <mutex>

struct sqlite3;

namespace Pathfinder {

// SQLite-backed knowledge store.
// Thread-safety: serialized via internal mutex; safe to call from any thread.
class AgentMemory {
public:
    explicit AgentMemory(const std::string& db_path);
    ~AgentMemory();

    AgentMemory(const AgentMemory&) = delete;
    AgentMemory& operator=(const AgentMemory&) = delete;

    // ── Cases ────────────────────────────────────────────────────────────
    void upsert_case(const CaseRecord& rec);
    std::optional<CaseRecord>      get_case(const std::string& case_id) const;
    std::vector<CaseRecord>        list_cases(const std::string& status = "") const;

    // ── Facts ────────────────────────────────────────────────────────────
    // Upsert: if a fact with same (case_id, type, key) exists, old value
    // is moved to history before update.
    void upsert_fact(const CaseFact& fact);

    std::vector<CaseFact> get_facts(const std::string& case_id,
                                    FactType type) const;
    std::vector<CaseFact> get_all_facts(const std::string& case_id) const;

    // Returns facts of type KeyEvent ordered by extracted_at — chronology.
    std::vector<CaseFact> get_chronology(const std::string& case_id) const;

    // Returns facts approaching deadlines within next N days.
    std::vector<CaseFact> get_upcoming_deadlines(int within_days = 14) const;

    // ── File index ───────────────────────────────────────────────────────
    // Returns true if the file has changed since last processing.
    bool needs_processing(const std::string& path,
                          int64_t current_mtime_ms) const;
    void mark_processed(const std::string& path,
                        int64_t mtime_ms,
                        const std::string& case_id);

    // ── Watched folders ──────────────────────────────────────────────────
    void        set_watched_folders(const std::vector<std::string>& paths);
    std::vector<std::string> get_watched_folders() const;

    // ── Maintenance ──────────────────────────────────────────────────────
    void evict_old_facts(int ttl_days);
    void vacuum();

private:
    void apply_schema();

    sqlite3*           m_db = nullptr;
    mutable std::mutex m_mx;
};

}  // namespace Pathfinder
