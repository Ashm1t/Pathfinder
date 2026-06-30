#include "workflow/WorkflowEngine.h"
#include "pipeline/CaseExtractor.h"
#include <fstream>
#include <chrono>
#include <iostream>

namespace Pathfinder {

static int64_t now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(
        system_clock::now().time_since_epoch()).count();
}

WorkflowEngine::WorkflowEngine(ILlmAdapter&   llm,
                               AgentMemory&   memory,
                               FilesystemMcp& fs)
    : m_llm(llm), m_memory(memory), m_fs(fs) {}

void WorkflowEngine::register_workflow(Workflow wf) {
    std::lock_guard<std::mutex> lk(m_mx);
    m_workflows.push_back(std::move(wf));
}

void WorkflowEngine::load_from_file(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return;

    json j;
    try { f >> j; } catch (...) { return; }

    for (auto& wj : j) {
        Workflow wf;
        wf.id          = wj.value("id", "");
        wf.name        = wj.value("name", "");
        wf.description = wj.value("description", "");
        wf.enabled     = wj.value("enabled", true);

        auto& tj = wj["trigger"];
        std::string tt = tj.value("type", "manual");
        if      (tt == "file_created")  wf.trigger.type = TriggerType::FileCreated;
        else if (tt == "file_modified") wf.trigger.type = TriggerType::FileModified;
        else if (tt == "schedule")      wf.trigger.type = TriggerType::Schedule;
        else if (tt == "deadline")      wf.trigger.type = TriggerType::Deadline;
        else                            wf.trigger.type = TriggerType::Manual;
        wf.trigger.config = tj.value("config", json::object());

        for (auto& sj : wj["steps"]) {
            WorkflowStep step;
            std::string st = sj.value("type", "llm_transform");
            if      (st == "read_document")  step.type = StepType::ReadDocument;
            else if (st == "extract_facts")  step.type = StepType::ExtractFacts;
            else if (st == "llm_transform")  step.type = StepType::LlmTransform;
            else if (st == "generate_doc")   step.type = StepType::GenerateDocument;
            else if (st == "write_file")     step.type = StepType::WriteFile;
            else                             step.type = StepType::Notify;
            step.name   = sj.value("name", "");
            step.config = sj.value("config", json::object());
            wf.steps.push_back(step);
        }
        register_workflow(std::move(wf));
    }
}

WorkflowResult WorkflowEngine::run(const std::string& workflow_id,
                                    WorkflowContext ctx) {
    // Copy the workflow out under lock, then release before executing —
    // step execution can block on the LLM for tens of seconds and must not
    // hold the registry mutex.
    Workflow wf;
    bool found = false;
    {
        std::lock_guard<std::mutex> lk(m_mx);
        for (auto& w : m_workflows) {
            if (w.id == workflow_id && w.enabled) { wf = w; found = true; break; }
        }
    }
    if (!found)
        return {false, "Workflow not found: " + workflow_id, ctx};

    ctx.workflow_id = workflow_id;
    return execute_steps(wf, ctx);
}

WorkflowResult WorkflowEngine::execute_steps(Workflow& wf, WorkflowContext& ctx) {
    WorkflowResult result;
    result.ctx = ctx;

    for (auto& step : wf.steps) {
        ctx.log.push_back("[" + step.name + "] starting");
        bool ok = false;
        switch (step.type) {
            case StepType::ReadDocument:   ok = step_read_document(step,  ctx); break;
            case StepType::ExtractFacts:   ok = step_extract_facts(step,  ctx); break;
            case StepType::LlmTransform:   ok = step_llm_transform(step,  ctx); break;
            case StepType::GenerateDocument: ok = step_generate_doc(step, ctx); break;
            case StepType::WriteFile:      ok = step_write_file(step,     ctx); break;
            case StepType::Notify:         ok = step_notify(step,         ctx); break;
        }
        if (!ok) {
            result.success = false;
            result.error   = "Step failed: " + step.name;
            result.ctx     = ctx;
            return result;
        }
        ctx.log.push_back("[" + step.name + "] done");
    }

    result.success = true;
    result.ctx     = ctx;
    return result;
}

// ── Step runners ─────────────────────────────────────────────────────────────

bool WorkflowEngine::step_read_document(const WorkflowStep& s,
                                         WorkflowContext& ctx) {
    std::string path = s.config.value("path", ctx.trigger_path);
    if (path.empty()) return false;

    std::string text = m_fs.read_file(path);
    if (text.empty()) return false;

    ctx.data["document_text"] = text;
    ctx.data["document_path"] = path;
    return true;
}

bool WorkflowEngine::step_extract_facts(const WorkflowStep& s,
                                         WorkflowContext& ctx) {
    std::string text = ctx.data.value("document_text", "");
    std::string path = ctx.data.value("document_path", "");
    if (text.empty()) return false;

    CaseExtractor extractor(m_llm);
    auto facts = extractor.extract(ctx.case_id, path, text);

    for (auto& f : facts)
        m_memory.upsert_fact(f);

    ctx.data["extracted_fact_count"] = facts.size();
    return true;
}

bool WorkflowEngine::step_llm_transform(const WorkflowStep& s,
                                         WorkflowContext& ctx) {
    std::string system_prompt = s.config.value("system_prompt", "");
    std::string user_template = s.config.value("user_template", "");
    std::string output_key    = s.config.value("output_key", "llm_output");

    // Simple template substitution: {{key}} → ctx.data[key]
    for (auto& [k, v] : ctx.data.items()) {
        std::string placeholder = "{{" + k + "}}";
        std::string val = v.is_string() ? v.get<std::string>() : v.dump();
        size_t pos;
        while ((pos = user_template.find(placeholder)) != std::string::npos)
            user_template.replace(pos, placeholder.size(), val);
    }

    LlmRequest req;
    req.temperature = s.config.value("temperature", 0.2f);
    req.max_tokens  = s.config.value("max_tokens",  1024);
    req.messages    = {
        {"system", system_prompt},
        {"user",   user_template}
    };

    auto resp = m_llm.complete(req);
    if (!resp.success) return false;

    ctx.data[output_key] = resp.content;
    return true;
}

bool WorkflowEngine::step_generate_doc(const WorkflowStep& s,
                                        WorkflowContext& ctx) {
    // Applies a text template and stores result as document content.
    std::string tmpl       = s.config.value("template", "");
    std::string output_key = s.config.value("output_key", "generated_doc");

    for (auto& [k, v] : ctx.data.items()) {
        std::string placeholder = "{{" + k + "}}";
        std::string val = v.is_string() ? v.get<std::string>() : v.dump();
        size_t pos;
        while ((pos = tmpl.find(placeholder)) != std::string::npos)
            tmpl.replace(pos, placeholder.size(), val);
    }

    // Also substitute from memory facts if case_id known
    if (!ctx.case_id.empty()) {
        auto facts = m_memory.get_all_facts(ctx.case_id);
        for (auto& f : facts) {
            std::string key = "{{" + fact_type_str(f.type) + "}}";
            size_t pos;
            while ((pos = tmpl.find(key)) != std::string::npos)
                tmpl.replace(pos, key.size(), f.value);
        }
    }

    ctx.data[output_key] = tmpl;
    return true;
}

bool WorkflowEngine::step_write_file(const WorkflowStep& s,
                                      WorkflowContext& ctx) {
    std::string content_key = s.config.value("content_key", "generated_doc");
    std::string output_path = s.config.value("output_path", "");

    if (output_path.empty()) return false;

    std::string content = ctx.data.value(content_key, "");
    if (content.empty()) return false;

    return m_fs.write_file(output_path, content);
}

bool WorkflowEngine::step_notify(const WorkflowStep& s, WorkflowContext& ctx) {
    // Stores a notification message in ctx.data for AgentLoop to pick up.
    std::string message = s.config.value("message", "Workflow completed");
    std::string severity = s.config.value("severity", "info");
    ctx.data["notification_message"]  = message;
    ctx.data["notification_severity"] = severity;
    return true;
}

// ── Dispatch ─────────────────────────────────────────────────────────────────

void WorkflowEngine::dispatch_file_event(const std::string& path,
                                          FileChangeEvent::Type event_type) {
    // Collect matching workflows under lock, then run them after releasing it.
    std::vector<Workflow> matched;
    {
        std::lock_guard<std::mutex> lk(m_mx);
        for (auto& wf : m_workflows) {
            if (!wf.enabled) continue;
            bool match =
                (event_type == FileChangeEvent::Type::Created &&
                 wf.trigger.type == TriggerType::FileCreated) ||
                (event_type == FileChangeEvent::Type::Modified &&
                 wf.trigger.type == TriggerType::FileModified);
            if (!match) continue;

            std::string glob = wf.trigger.config.value("glob", "");
            if (!glob.empty() && path.find(glob) == std::string::npos) continue;

            matched.push_back(wf);
        }
    }

    for (auto& wf : matched) {
        WorkflowContext ctx;
        ctx.trigger_path = path;
        execute_steps(wf, ctx);
    }
}

void WorkflowEngine::dispatch_deadline_check() {
    auto deadlines = m_memory.get_upcoming_deadlines(14);
    if (deadlines.empty()) return;

    std::vector<Workflow> matched;
    {
        std::lock_guard<std::mutex> lk(m_mx);
        for (auto& wf : m_workflows)
            if (wf.enabled && wf.trigger.type == TriggerType::Deadline)
                matched.push_back(wf);
    }

    for (auto& wf : matched) {
        for (auto& d : deadlines) {
            WorkflowContext ctx;
            ctx.case_id = d.case_id;
            ctx.data["deadline_type"]  = fact_type_str(d.type);
            ctx.data["deadline_value"] = d.value;
            execute_steps(wf, ctx);
        }
    }
}

}  // namespace Pathfinder
