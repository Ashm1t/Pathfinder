#include "memory/AgentMemory.h"
#include <sqlite3.h>
#include <stdexcept>
#include <chrono>
#include <cstdio>

namespace Pathfinder {

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(
        system_clock::now().time_since_epoch()).count();
}

// ── Date parsing ─────────────────────────────────────────────────────────────

int64_t iso_date_to_ms(const std::string& iso) {
    int y = 0, m = 0, d = 0;
    if (std::sscanf(iso.c_str(), "%d-%d-%d", &y, &m, &d) != 3) return 0;
    if (m < 1 || m > 12 || d < 1 || d > 31 || y < 1900 || y > 2200) return 0;

    // days_from_civil (Howard Hinnant) — days since 1970-01-01, no timezone.
    y -= (m <= 2);
    const int era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yoe = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    const int64_t days = static_cast<int64_t>(era) * 146097 +
                         static_cast<int64_t>(doe) - 719468;
    return days * 86400000LL;
}

// ── Schema ───────────────────────────────────────────────────────────────────

static constexpr const char* SCHEMA = R"SQL(
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    case_id        TEXT PRIMARY KEY,
    title          TEXT,
    fir_number     TEXT,
    police_station TEXT,
    status         TEXT DEFAULT 'active',
    io_name        TEXT,
    created_at     INTEGER,
    updated_at     INTEGER
);

CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT    NOT NULL,
    fact_type     TEXT    NOT NULL,
    key           TEXT    NOT NULL DEFAULT '',
    value         TEXT    NOT NULL,
    source_file   TEXT,
    source_page   INTEGER DEFAULT 0,
    confidence    REAL    DEFAULT 1.0,
    extracted_at  INTEGER,
    event_date_ms INTEGER DEFAULT 0,
    FOREIGN KEY(case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS fact_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id    INTEGER NOT NULL,
    old_value  TEXT,
    changed_at INTEGER,
    FOREIGN KEY(fact_id) REFERENCES facts(id)
);

CREATE TABLE IF NOT EXISTS file_index (
    path           TEXT PRIMARY KEY,
    last_mtime_ms  INTEGER,
    last_processed INTEGER,
    case_id        TEXT
);

CREATE TABLE IF NOT EXISTS watched_folders (
    path    TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_facts_case  ON facts(case_id);
CREATE INDEX IF NOT EXISTS idx_facts_type  ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_event ON facts(event_date_ms);
)SQL";

// ── Helpers ──────────────────────────────────────────────────────────────────

static void exec(sqlite3* db, const std::string& sql) {
    char* err = nullptr;
    if (sqlite3_exec(db, sql.c_str(), nullptr, nullptr, &err) != SQLITE_OK) {
        std::string msg = err ? err : "unknown";
        sqlite3_free(err);
        throw std::runtime_error("SQLite exec failed: " + msg);
    }
}

// Prepare a statement or throw. Eliminates the unchecked-prepare crash risk.
static sqlite3_stmt* prepare(sqlite3* db, const char* sql) {
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        std::string msg = sqlite3_errmsg(db);
        throw std::runtime_error("SQLite prepare failed: " + msg +
                                 "\nSQL: " + sql);
    }
    return stmt;
}

static std::string col_text(sqlite3_stmt* s, int i) {
    auto p = reinterpret_cast<const char*>(sqlite3_column_text(s, i));
    return p ? std::string(p) : std::string{};
}

// Read a full CaseFact row from a SELECT with the canonical column order:
// id, case_id, fact_type, key, value, source_file, source_page,
// confidence, extracted_at, event_date_ms
static CaseFact read_fact_row(sqlite3_stmt* s) {
    CaseFact f;
    f.id           = sqlite3_column_int64(s, 0);
    f.case_id      = col_text(s, 1);
    f.type         = fact_type_from_str(col_text(s, 2));
    f.key          = col_text(s, 3);
    f.value        = col_text(s, 4);
    f.source_file  = col_text(s, 5);
    f.source_page  = sqlite3_column_int(s, 6);
    f.confidence   = static_cast<float>(sqlite3_column_double(s, 7));
    f.extracted_at = sqlite3_column_int64(s, 8);
    f.event_date_ms = sqlite3_column_int64(s, 9);
    return f;
}

static constexpr const char* FACT_COLS =
    "id,case_id,fact_type,key,value,source_file,source_page,"
    "confidence,extracted_at,event_date_ms";

// ── AgentMemory ──────────────────────────────────────────────────────────────

AgentMemory::AgentMemory(const std::string& db_path) {
    if (sqlite3_open(db_path.c_str(), &m_db) != SQLITE_OK)
        throw std::runtime_error("Cannot open database: " + db_path);
    apply_schema();
}

AgentMemory::~AgentMemory() {
    if (m_db) sqlite3_close(m_db);
}

void AgentMemory::apply_schema() {
    exec(m_db, SCHEMA);
}

// ── Cases ────────────────────────────────────────────────────────────────────

void AgentMemory::upsert_case(const CaseRecord& rec) {
    std::lock_guard<std::mutex> lk(m_mx);
    const char* sql = R"SQL(
        INSERT INTO cases(case_id, title, fir_number, police_station,
                          status, io_name, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(case_id) DO UPDATE SET
            title=excluded.title, fir_number=excluded.fir_number,
            police_station=excluded.police_station,
            status=excluded.status, io_name=excluded.io_name,
            updated_at=excluded.updated_at;
    )SQL";

    sqlite3_stmt* stmt = prepare(m_db, sql);
    sqlite3_bind_text(stmt, 1, rec.case_id.c_str(),       -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, rec.title.c_str(),          -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 3, rec.fir_number.c_str(),     -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 4, rec.police_station.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 5, rec.status.c_str(),         -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 6, rec.io_name.c_str(),        -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(stmt, 7, rec.created_at ? rec.created_at : now_ms());
    sqlite3_bind_int64(stmt, 8, now_ms());
    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
}

std::optional<CaseRecord> AgentMemory::get_case(const std::string& case_id) const {
    std::lock_guard<std::mutex> lk(m_mx);
    const char* sql =
        "SELECT case_id,title,fir_number,police_station,status,io_name,"
        "created_at,updated_at FROM cases WHERE case_id=?";
    sqlite3_stmt* stmt = prepare(m_db, sql);
    sqlite3_bind_text(stmt, 1, case_id.c_str(), -1, SQLITE_TRANSIENT);

    std::optional<CaseRecord> out;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        CaseRecord rec;
        rec.case_id        = col_text(stmt, 0);
        rec.title          = col_text(stmt, 1);
        rec.fir_number     = col_text(stmt, 2);
        rec.police_station = col_text(stmt, 3);
        rec.status         = col_text(stmt, 4);
        rec.io_name        = col_text(stmt, 5);
        rec.created_at     = sqlite3_column_int64(stmt, 6);
        rec.updated_at     = sqlite3_column_int64(stmt, 7);
        out = rec;
    }
    sqlite3_finalize(stmt);
    return out;
}

std::vector<CaseRecord> AgentMemory::list_cases(const std::string& status) const {
    std::lock_guard<std::mutex> lk(m_mx);
    std::string sql =
        "SELECT case_id,title,fir_number,police_station,status,io_name,"
        "created_at,updated_at FROM cases";
    if (!status.empty()) sql += " WHERE status=?";
    sql += " ORDER BY updated_at DESC";

    sqlite3_stmt* stmt = prepare(m_db, sql.c_str());
    if (!status.empty())
        sqlite3_bind_text(stmt, 1, status.c_str(), -1, SQLITE_TRANSIENT);

    std::vector<CaseRecord> recs;
    while (sqlite3_step(stmt) == SQLITE_ROW) {
        CaseRecord r;
        r.case_id        = col_text(stmt, 0); r.title = col_text(stmt, 1);
        r.fir_number     = col_text(stmt, 2); r.police_station = col_text(stmt, 3);
        r.status         = col_text(stmt, 4); r.io_name = col_text(stmt, 5);
        r.created_at     = sqlite3_column_int64(stmt, 6);
        r.updated_at     = sqlite3_column_int64(stmt, 7);
        recs.push_back(r);
    }
    sqlite3_finalize(stmt);
    return recs;
}

// ── Facts ────────────────────────────────────────────────────────────────────

void AgentMemory::upsert_fact(const CaseFact& fact) {
    std::lock_guard<std::mutex> lk(m_mx);
    std::string type_str = fact_type_str(fact.type);

    const char* find_sql =
        "SELECT id, value FROM facts WHERE case_id=? AND fact_type=? AND key=?";
    sqlite3_stmt* find = prepare(m_db, find_sql);
    sqlite3_bind_text(find, 1, fact.case_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(find, 2, type_str.c_str(),     -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(find, 3, fact.key.c_str(),     -1, SQLITE_TRANSIENT);

    if (sqlite3_step(find) == SQLITE_ROW) {
        int64_t existing_id = sqlite3_column_int64(find, 0);
        std::string old_value = col_text(find, 1);
        sqlite3_finalize(find);

        if (old_value != fact.value) {
            // Archive old value
            sqlite3_stmt* hist = prepare(m_db,
                "INSERT INTO fact_history(fact_id, old_value, changed_at) "
                "VALUES(?,?,?)");
            sqlite3_bind_int64(hist, 1, existing_id);
            sqlite3_bind_text(hist,  2, old_value.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int64(hist, 3, now_ms());
            sqlite3_step(hist);
            sqlite3_finalize(hist);

            sqlite3_stmt* u = prepare(m_db,
                "UPDATE facts SET value=?,source_file=?,source_page=?,"
                "confidence=?,extracted_at=?,event_date_ms=? WHERE id=?");
            sqlite3_bind_text(u,  1, fact.value.c_str(),       -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(u,  2, fact.source_file.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_int(u,   3, fact.source_page);
            sqlite3_bind_double(u,4, fact.confidence);
            sqlite3_bind_int64(u, 5, now_ms());
            sqlite3_bind_int64(u, 6, fact.event_date_ms);
            sqlite3_bind_int64(u, 7, existing_id);
            sqlite3_step(u);
            sqlite3_finalize(u);
        }
    } else {
        sqlite3_finalize(find);
        sqlite3_stmt* s = prepare(m_db,
            "INSERT INTO facts(case_id,fact_type,key,value,source_file,"
            "source_page,confidence,extracted_at,event_date_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?)");
        sqlite3_bind_text(s,  1, fact.case_id.c_str(),     -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(s,  2, type_str.c_str(),          -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(s,  3, fact.key.c_str(),          -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(s,  4, fact.value.c_str(),        -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(s,  5, fact.source_file.c_str(),  -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(s,   6, fact.source_page);
        sqlite3_bind_double(s,7, fact.confidence);
        sqlite3_bind_int64(s, 8, now_ms());
        sqlite3_bind_int64(s, 9, fact.event_date_ms);
        sqlite3_step(s);
        sqlite3_finalize(s);
    }
}

std::vector<CaseFact> AgentMemory::get_facts(const std::string& case_id,
                                               FactType type) const {
    std::lock_guard<std::mutex> lk(m_mx);
    std::string sql = std::string("SELECT ") + FACT_COLS +
                      " FROM facts WHERE case_id=? AND fact_type=?";
    sqlite3_stmt* s = prepare(m_db, sql.c_str());
    sqlite3_bind_text(s, 1, case_id.c_str(),              -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(s, 2, fact_type_str(type).c_str(),  -1, SQLITE_TRANSIENT);

    std::vector<CaseFact> facts;
    while (sqlite3_step(s) == SQLITE_ROW)
        facts.push_back(read_fact_row(s));
    sqlite3_finalize(s);
    return facts;
}

std::vector<CaseFact> AgentMemory::get_all_facts(const std::string& case_id) const {
    std::lock_guard<std::mutex> lk(m_mx);
    std::string sql = std::string("SELECT ") + FACT_COLS +
                      " FROM facts WHERE case_id=?";
    sqlite3_stmt* s = prepare(m_db, sql.c_str());
    sqlite3_bind_text(s, 1, case_id.c_str(), -1, SQLITE_TRANSIENT);

    std::vector<CaseFact> facts;
    while (sqlite3_step(s) == SQLITE_ROW)
        facts.push_back(read_fact_row(s));
    sqlite3_finalize(s);
    return facts;
}

std::vector<CaseFact> AgentMemory::get_chronology(const std::string& case_id) const {
    std::lock_guard<std::mutex> lk(m_mx);
    // Order by the real-world event date when known, else by extraction time.
    std::string sql = std::string("SELECT ") + FACT_COLS +
        " FROM facts WHERE case_id=? AND fact_type='KeyEvent' "
        "ORDER BY (CASE WHEN event_date_ms>0 THEN event_date_ms "
        "          ELSE extracted_at END) ASC";
    sqlite3_stmt* s = prepare(m_db, sql.c_str());
    sqlite3_bind_text(s, 1, case_id.c_str(), -1, SQLITE_TRANSIENT);

    std::vector<CaseFact> facts;
    while (sqlite3_step(s) == SQLITE_ROW)
        facts.push_back(read_fact_row(s));
    sqlite3_finalize(s);
    return facts;
}

std::vector<CaseFact> AgentMemory::get_upcoming_deadlines(int within_days) const {
    std::lock_guard<std::mutex> lk(m_mx);
    const int64_t now   = now_ms();
    const int64_t limit = now + static_cast<int64_t>(within_days) * 86400LL * 1000LL;

    // Only deadlines with a parsed date that falls within [now, limit].
    std::string sql = std::string("SELECT ") + FACT_COLS +
        " FROM facts WHERE fact_type IN ('ChargesheetDeadline','CourtDate') "
        "AND event_date_ms BETWEEN ? AND ? "
        "ORDER BY event_date_ms ASC";
    sqlite3_stmt* s = prepare(m_db, sql.c_str());
    sqlite3_bind_int64(s, 1, now);
    sqlite3_bind_int64(s, 2, limit);

    std::vector<CaseFact> facts;
    while (sqlite3_step(s) == SQLITE_ROW)
        facts.push_back(read_fact_row(s));
    sqlite3_finalize(s);
    return facts;
}

// ── File index ───────────────────────────────────────────────────────────────

bool AgentMemory::needs_processing(const std::string& path,
                                    int64_t current_mtime_ms) const {
    std::lock_guard<std::mutex> lk(m_mx);
    sqlite3_stmt* s = prepare(m_db,
        "SELECT last_mtime_ms FROM file_index WHERE path=?");
    sqlite3_bind_text(s, 1, path.c_str(), -1, SQLITE_TRANSIENT);

    bool changed = true;
    if (sqlite3_step(s) == SQLITE_ROW)
        changed = sqlite3_column_int64(s, 0) != current_mtime_ms;
    sqlite3_finalize(s);
    return changed;
}

void AgentMemory::mark_processed(const std::string& path,
                                  int64_t mtime_ms,
                                  const std::string& case_id) {
    std::lock_guard<std::mutex> lk(m_mx);
    sqlite3_stmt* s = prepare(m_db,
        "INSERT INTO file_index(path, last_mtime_ms, last_processed, case_id) "
        "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
        "last_mtime_ms=excluded.last_mtime_ms,"
        "last_processed=excluded.last_processed,"
        "case_id=excluded.case_id");
    sqlite3_bind_text(s,  1, path.c_str(),    -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(s, 2, mtime_ms);
    sqlite3_bind_int64(s, 3, now_ms());
    sqlite3_bind_text(s,  4, case_id.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_step(s);
    sqlite3_finalize(s);
}

// ── Watched folders ──────────────────────────────────────────────────────────

void AgentMemory::set_watched_folders(const std::vector<std::string>& paths) {
    std::lock_guard<std::mutex> lk(m_mx);
    exec(m_db, "DELETE FROM watched_folders");
    for (auto& p : paths) {
        sqlite3_stmt* s = prepare(m_db,
            "INSERT OR IGNORE INTO watched_folders(path, enabled) VALUES(?,1)");
        sqlite3_bind_text(s, 1, p.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_step(s);
        sqlite3_finalize(s);
    }
}

std::vector<std::string> AgentMemory::get_watched_folders() const {
    std::lock_guard<std::mutex> lk(m_mx);
    sqlite3_stmt* s = prepare(m_db,
        "SELECT path FROM watched_folders WHERE enabled=1");
    std::vector<std::string> paths;
    while (sqlite3_step(s) == SQLITE_ROW)
        paths.push_back(col_text(s, 0));
    sqlite3_finalize(s);
    return paths;
}

// ── Maintenance ──────────────────────────────────────────────────────────────

void AgentMemory::evict_old_facts(int ttl_days) {
    std::lock_guard<std::mutex> lk(m_mx);
    int64_t cutoff = now_ms() - static_cast<int64_t>(ttl_days) * 86400LL * 1000LL;
    sqlite3_stmt* s = prepare(m_db,
        "DELETE FROM facts WHERE extracted_at < ? "
        "AND case_id IN (SELECT case_id FROM cases WHERE status='closed')");
    sqlite3_bind_int64(s, 1, cutoff);
    sqlite3_step(s);
    sqlite3_finalize(s);
}

void AgentMemory::vacuum() {
    std::lock_guard<std::mutex> lk(m_mx);
    exec(m_db, "VACUUM");
}

// ── FactType string map ──────────────────────────────────────────────────────

std::string fact_type_str(FactType t) {
    switch (t) {
        case FactType::CaseTitle:             return "CaseTitle";
        case FactType::FirNumber:             return "FirNumber";
        case FactType::PoliceStation:         return "PoliceStation";
        case FactType::District:              return "District";
        case FactType::DateOfIncident:        return "DateOfIncident";
        case FactType::DateOfFIR:             return "DateOfFIR";
        case FactType::AccusedName:           return "AccusedName";
        case FactType::AccusedAddress:        return "AccusedAddress";
        case FactType::WitnessName:           return "WitnessName";
        case FactType::VictimName:            return "VictimName";
        case FactType::IpcSection:            return "IpcSection";
        case FactType::ChargesheetDeadline:   return "ChargesheetDeadline";
        case FactType::CourtDate:             return "CourtDate";
        case FactType::IoName:                return "IoName";
        case FactType::CaseStatus:            return "CaseStatus";
        case FactType::NoticeIssued:          return "NoticeIssued";
        case FactType::NoticeResponse:        return "NoticeResponse";
        case FactType::SeizedProperty:        return "SeizedProperty";
        case FactType::KeyEvent:              return "KeyEvent";
        case FactType::WorkflowStep:          return "WorkflowStep";
        default:                              return "Unknown";
    }
}

FactType fact_type_from_str(const std::string& s) {
    if (s == "CaseTitle")           return FactType::CaseTitle;
    if (s == "FirNumber")           return FactType::FirNumber;
    if (s == "PoliceStation")       return FactType::PoliceStation;
    if (s == "District")            return FactType::District;
    if (s == "DateOfIncident")      return FactType::DateOfIncident;
    if (s == "DateOfFIR")           return FactType::DateOfFIR;
    if (s == "AccusedName")         return FactType::AccusedName;
    if (s == "AccusedAddress")      return FactType::AccusedAddress;
    if (s == "WitnessName")         return FactType::WitnessName;
    if (s == "VictimName")          return FactType::VictimName;
    if (s == "IpcSection")          return FactType::IpcSection;
    if (s == "ChargesheetDeadline") return FactType::ChargesheetDeadline;
    if (s == "CourtDate")           return FactType::CourtDate;
    if (s == "IoName")              return FactType::IoName;
    if (s == "CaseStatus")          return FactType::CaseStatus;
    if (s == "NoticeIssued")        return FactType::NoticeIssued;
    if (s == "NoticeResponse")      return FactType::NoticeResponse;
    if (s == "SeizedProperty")      return FactType::SeizedProperty;
    if (s == "KeyEvent")            return FactType::KeyEvent;
    if (s == "WorkflowStep")        return FactType::WorkflowStep;
    return FactType::KeyEvent;
}

}  // namespace Pathfinder
