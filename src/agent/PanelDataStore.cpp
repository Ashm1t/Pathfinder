#include "agent/PanelDataStore.h"

namespace Pathfinder {

void PanelDataStore::set_recent_cases(std::vector<RecentCaseItem> items) {
    { std::lock_guard<std::mutex> lk(m_mx); m_recent_cases = std::move(items); }
    notify();
}

void PanelDataStore::set_updates(std::vector<UpdateItem> items) {
    { std::lock_guard<std::mutex> lk(m_mx); m_updates = std::move(items); }
    notify();
}

void PanelDataStore::set_chronology(std::string case_id,
                                     std::vector<ChronologyEntry> entries) {
    {
        std::lock_guard<std::mutex> lk(m_mx);
        m_chronology[case_id] = std::move(entries);
    }
    notify();
}

void PanelDataStore::set_whats_next(std::vector<WhatsNextItem> items) {
    { std::lock_guard<std::mutex> lk(m_mx); m_whats_next = std::move(items); }
    notify();
}

void PanelDataStore::push_update(UpdateItem item) {
    {
        std::lock_guard<std::mutex> lk(m_mx);
        m_updates.insert(m_updates.begin(), std::move(item));
        // Keep at most 50 updates
        if (m_updates.size() > 50)
            m_updates.resize(50);
    }
    notify();
}

std::vector<RecentCaseItem> PanelDataStore::get_recent_cases() const {
    std::lock_guard<std::mutex> lk(m_mx);
    return m_recent_cases;
}

std::vector<UpdateItem> PanelDataStore::get_updates() const {
    std::lock_guard<std::mutex> lk(m_mx);
    return m_updates;
}

std::vector<ChronologyEntry> PanelDataStore::get_chronology(
    const std::string& case_id) const
{
    std::lock_guard<std::mutex> lk(m_mx);
    auto it = m_chronology.find(case_id);
    if (it != m_chronology.end()) return it->second;
    return {};
}

std::vector<WhatsNextItem> PanelDataStore::get_whats_next() const {
    std::lock_guard<std::mutex> lk(m_mx);
    return m_whats_next;
}

void PanelDataStore::notify() const {
    if (m_change_cb) m_change_cb();
}

}  // namespace Pathfinder
