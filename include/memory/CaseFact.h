#pragma once
#include <string>
#include <cstdint>

namespace Pathfinder {

// Canonical fact types extracted from case documents.
enum class FactType {
    CaseTitle,
    FirNumber,
    PoliceStation,
    District,
    DateOfIncident,
    DateOfFIR,
    AccusedName,
    AccusedAddress,
    WitnessName,
    VictimName,
    IpcSection,       // BNS / IPC section applied
    ChargesheetDeadline,
    CourtDate,
    IoName,           // Investigating Officer
    CaseStatus,       // active | chargesheeted | closed | stayed
    NoticeIssued,
    NoticeResponse,
    SeizedProperty,
    KeyEvent,         // general chronology event
    WorkflowStep,     // agent-generated workflow tracking
};

std::string fact_type_str(FactType t);
FactType    fact_type_from_str(const std::string& s);

struct CaseFact {
    int64_t     id            = 0;  // DB row id; 0 = not yet persisted
    std::string case_id;
    FactType    type;
    std::string key;          // sub-key for multi-value types (e.g. accused index)
    std::string value;
    std::string source_file;  // absolute path to source document
    int         source_page   = 0;
    float       confidence    = 1.0f;
    int64_t     extracted_at  = 0;  // Unix ms — when the agent extracted this
    int64_t     event_date_ms = 0;  // Unix ms — the real-world date the fact
                                    // refers to (incident/deadline/court date);
                                    // 0 if the document carried no date.
};

// Parse an ISO date ("YYYY-MM-DD") to Unix epoch milliseconds (UTC midnight).
// Returns 0 if the string is not a valid ISO date. Uses a timezone-free
// civil-date algorithm (no mktime / no locale dependence).
int64_t iso_date_to_ms(const std::string& iso);

struct CaseRecord {
    std::string case_id;
    std::string title;
    std::string fir_number;
    std::string police_station;
    std::string status;       // active | chargesheeted | closed
    std::string io_name;
    int64_t     created_at   = 0;
    int64_t     updated_at   = 0;
};

}  // namespace Pathfinder
