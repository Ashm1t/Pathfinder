#pragma once
#include "llm/ILlmAdapter.h"
#include "memory/AgentMemory.h"
#include "mcp/FilesystemMcp.h"
#include <string>
#include <vector>
#include <functional>
#include <nlohmann/json.hpp>

namespace Pathfinder {

using json = nlohmann::json;

// ── Step types ───────────────────────────────────────────────────────────────
// Each step is a named operation with a JSON config block.
// Steps are composable and run sequentially; output of one feeds the next.

enum class StepType {
    ReadDocument,        // Read file(s) from filesystem MCP
    ExtractFacts,        // Run CaseExtractor on text
    LlmTransform,        // Freeform LLM prompt with context
    GenerateDocument,    // Render a template with extracted facts
    WriteFile,           // Write output via filesystem MCP
    Notify,              // Push a notification to PanelDataStore
};

struct WorkflowStep {
    StepType    type;
    std::string name;   // human label, for logging
    json        config;
};

// ── Trigger types ────────────────────────────────────────────────────────────
enum class TriggerType {
    FileCreated,     // new file appears in watched folder
    FileModified,    // existing file changes
    Schedule,        // cron-like timer
    Deadline,        // N days before a deadline fact
    Manual,          // explicitly called by AgentLoop
};

struct WorkflowTrigger {
    TriggerType type;
    json        config;  // type-specific params (e.g. folder glob, cron expr)
};

// ── Workflow ─────────────────────────────────────────────────────────────────
struct Workflow {
    std::string              id;
    std::string              name;
    std::string              description;
    WorkflowTrigger          trigger;
    std::vector<WorkflowStep> steps;
    bool                     enabled = true;
};

// ── Execution context ─────────────────────────────────────────────────────────
// Passed through steps; each step reads from and writes to this context.
struct WorkflowContext {
    std::string              workflow_id;
    std::string              case_id;      // may be empty for non-case workflows
    std::string              trigger_path; // file that triggered, if any
    json                     data;         // accumulated step outputs
    std::vector<std::string> log;
};

// ── Result ───────────────────────────────────────────────────────────────────
struct WorkflowResult {
    bool        success = false;
    std::string error;
    WorkflowContext ctx;
};

// ── Engine ───────────────────────────────────────────────────────────────────
class WorkflowEngine {
public:
    WorkflowEngine(ILlmAdapter&   llm,
                   AgentMemory&   memory,
                   FilesystemMcp& fs);

    // Register a workflow definition.
    void register_workflow(Workflow wf);

    // Load workflow definitions from a JSON file.
    void load_from_file(const std::string& path);

    // Execute a workflow by ID with a given context seed.
    WorkflowResult run(const std::string& workflow_id,
                       WorkflowContext    ctx);

    // Check all file-triggered workflows against a changed path.
    // Called by AgentLoop on every file change event.
    void dispatch_file_event(const std::string& path,
                             FileChangeEvent::Type event_type);

    // Check deadline-triggered workflows against current time.
    // Called by AgentLoop on its periodic tick.
    void dispatch_deadline_check();

    const std::vector<Workflow>& workflows() const { return m_workflows; }

private:
    WorkflowResult execute_steps(Workflow& wf, WorkflowContext& ctx);

    // Step runners
    bool step_read_document  (const WorkflowStep& s, WorkflowContext& ctx);
    bool step_extract_facts  (const WorkflowStep& s, WorkflowContext& ctx);
    bool step_llm_transform  (const WorkflowStep& s, WorkflowContext& ctx);
    bool step_generate_doc   (const WorkflowStep& s, WorkflowContext& ctx);
    bool step_write_file     (const WorkflowStep& s, WorkflowContext& ctx);
    bool step_notify         (const WorkflowStep& s, WorkflowContext& ctx);

    ILlmAdapter&           m_llm;
    AgentMemory&           m_memory;
    FilesystemMcp&         m_fs;
    std::vector<Workflow>  m_workflows;
    std::mutex             m_mx;
};

}  // namespace Pathfinder
