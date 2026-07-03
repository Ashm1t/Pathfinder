"""End-to-end offline verification of the modernized workflow system.

Run from agent/:  python verify_workflows.py

No Ollama needed. Exercises: blueprint validation of all shipped workflows,
offline ingest (structural + header facts) of sample_data, real
WorkflowEngine runs of the three LLM-free workflows, deadline dispatch
honouring per-workflow trigger config, and validator rejection of bad
definitions (the safety boundary for LLM-authored workflows).
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from pathfinder_agent.analyzer import scan_and_ingest
from pathfinder_agent.config import Config
from pathfinder_agent.extraction import CaseExtractor
from pathfinder_agent.llm import make_llm_adapter
from pathfinder_agent.memory import AgentMemory
from pathfinder_agent.models import CaseFact, FactType, normalize_case_status
from pathfinder_agent.panels import all_panels
from pathfinder_agent.pipeline import DocumentPipeline
from pathfinder_agent.workflow import WorkflowEngine
from pathfinder_agent.workflow_schema import make_scaffold, validate_workflow

DB = "verify_workflows.db"
FAILURES: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def main() -> int:
    for f in (DB, DB + "-shm", DB + "-wal"):
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree("drafts", ignore_errors=True)

    cfg = Config.load("config/pathfinder.json")
    mem = AgentMemory(DB)
    llm = make_llm_adapter(cfg.llm)  # never called on the offline paths

    print("1. Blueprint validation of shipped workflows")
    with open("config/workflows.json", encoding="utf-8") as f:
        defs = json.load(f)
    check("4 workflows shipped", len(defs) == 4, f"got {len(defs)}")
    for wf in defs:
        errors = validate_workflow(wf)
        check(f"'{wf.get('id')}' validates", not errors, "; ".join(errors))
    check("scaffold validates", not validate_workflow(make_scaffold()))

    print("2. Validator rejects bad definitions")
    bad = make_scaffold("bad_flow")
    bad["steps"][1]["config"]["template"] = "uses {{nonexistent_key}}"
    check("unknown placeholder rejected", bool(validate_workflow(bad)))
    bad2 = make_scaffold("bad_flow2")
    bad2["steps"][2]["config"]["output_path"] = "C:/Windows/evil.txt"
    check("absolute output path rejected", bool(validate_workflow(bad2)))
    bad3 = make_scaffold("bad_flow3")
    bad3["steps"] = bad3["steps"][:-1]  # drop the notify
    check("missing final notify rejected", bool(validate_workflow(bad3)))

    print("3. Offline ingest of sample_data (structural + header facts)")
    n = scan_and_ingest(mem, "sample_data")
    pipeline = DocumentPipeline(mem, CaseExtractor(llm), cfg.agent)
    for root, _dirs, files in os.walk("sample_data"):
        for name in files:
            p = os.path.join(root, name)
            pipeline.process_file(p, int(os.path.getmtime(p) * 1000), use_llm=False)
    check("3 case folders ingested", n == 3, f"got {n}")

    key_events = mem.get_chronology("FIR 214-26")
    check("chronology no longer collapses (FIR 214-26 has >= 6 events)",
          len(key_events) >= 6, f"got {len(key_events)}")

    deadlines = mem.get_upcoming_deadlines(14)
    dtypes = {(d.case_id, d.type.value) for d in deadlines}
    check("chargesheet deadlines found WITHOUT LLM",
          ("FIR 214-26", "ChargesheetDeadline") in dtypes
          and ("FIR 302-26", "ChargesheetDeadline") in dtypes, str(dtypes))
    check("court date found WITHOUT LLM",
          ("FIR 214-26", "CourtDate") in dtypes, str(dtypes))

    witnesses = [f.value for f in mem.get_facts("FIR 302-26", FactType.WITNESS_NAME)]
    check("witnesses extracted from headers (FIR 302-26)",
          sorted(witnesses) == ["Anita Deshmukh", "Suresh Kalra"], str(witnesses))

    panels = all_panels(mem)
    check("major_updates panel now populated offline",
          len(panels["major_updates"]) >= 3, f"got {len(panels['major_updates'])}")
    check("whats_next panel now populated offline",
          len(panels["whats_next"]) >= 1, f"got {len(panels['whats_next'])}")

    print("4. Real engine runs of the LLM-free workflows")

    def resolver(case_id: str):
        d = os.path.join("sample_data", case_id)
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, x) for x in sorted(os.listdir(d))
                if os.path.isfile(os.path.join(d, x))]

    notifications = []
    engine = WorkflowEngine(llm, mem, notifications.append, resolver)
    engine.load_from_file("config/workflows.json")

    res = engine.run("notice_draft", {"case_id": "FIR 302-26"})
    check("notice_draft runs (FIR 302-26)", res.ok, res.error)
    check("2 witness notices written",
          os.path.exists("drafts/FIR 302-26 - notice - Suresh Kalra.txt")
          and os.path.exists("drafts/FIR 302-26 - notice - Anita Deshmukh.txt"))

    res = engine.run("notice_draft", {"case_id": "FIR 214-26"})
    check("notice_draft no-witness case is a clean no-op", res.ok
          and res.context.get("witness_count") == 0, res.error)

    res = engine.run("chargesheet_check", {
        "case_id": "FIR 214-26", "deadline_type": "ChargesheetDeadline",
        "deadline_value": "08.07.2026"})
    check("chargesheet_check runs (FIR 214-26)", res.ok, res.error)
    report = res.context.get("final_report", "")
    check("completeness report finds the missing witness statement",
          "[ ] Witness statement(s) on record" in report)
    check("completeness report confirms deadline stated",
          "[x] Chargesheet deadline stated" in report)

    res = engine.run("court_compliance", {
        "case_id": "FIR 214-26", "deadline_type": "CourtDate",
        "deadline_value": "05.07.2026"})
    check("court_compliance runs (FIR 214-26)", res.ok, res.error)
    check("compliance report carries real chronology",
          "Document on record" in res.context.get("final_report", ""))

    print("5. Deadline dispatch honours per-workflow trigger config")
    notifications.clear()
    engine.dispatch_deadline_check()
    by_sev = [n["severity"] for n in notifications]
    # chargesheet_check (7d window): only FIR 214-26 (5d) qualifies — FIR
    # 302-26 (10d) must NOT fire. court_compliance (3d): FIR 214-26 court
    # date (2d) qualifies.
    check("exactly 2 deadline workflows fired (7d + 3d windows respected)",
          len(notifications) == 2, str(notifications))
    check("severities are urgent + warning", sorted(by_sev) == ["urgent", "warning"],
          str(by_sev))

    print("5b. Memory-integration fixes")
    # (1) multi-valued facts with empty keys must accumulate, not collapse
    mem.upsert_fact(CaseFact(case_id="TEST-1", type=FactType.WITNESS_NAME,
                             value="Witness A"))
    mem.upsert_fact(CaseFact(case_id="TEST-1", type=FactType.WITNESS_NAME,
                             value="Witness B"))
    wvals = [f.value for f in mem.get_facts("TEST-1", FactType.WITNESS_NAME)]
    check("empty-key multi-valued facts accumulate (LLM producer path)",
          sorted(wvals) == ["Witness A", "Witness B"], str(wvals))

    # (2) CaseStatus facts promote to cases.status (and normalize)
    check("status normalization",
          normalize_case_status("Chargesheet filed on 01.07.2026") == "chargesheeted"
          and normalize_case_status("Case closed as untraced") == "closed"
          and normalize_case_status("Under investigation") == "active")
    rec = mem.get_case("FIR 214-26")
    check("ingest promoted PS/IO/status onto the case record",
          rec is not None and rec.police_station != "" and rec.io_name != ""
          and rec.status == "active",
          f"ps={rec.police_station!r} io={rec.io_name!r} status={rec.status!r}"
          if rec else "no record")

    # (3) notifications persist across a reopen
    mem.add_notification({"message": "persist-me", "severity": "info",
                          "case_id": "TEST-1"})
    mem.close()
    mem = AgentMemory(DB)
    notes = mem.list_notifications()
    check("notifications survive a restart",
          any(n["message"] == "persist-me" for n in notes), str(notes[:2]))

    # (4) fact_history is readable
    mem.upsert_fact(CaseFact(case_id="TEST-1", type=FactType.IO_NAME,
                             value="SI First"))
    mem.upsert_fact(CaseFact(case_id="TEST-1", type=FactType.IO_NAME,
                             value="Insp Second"))
    hist = mem.get_fact_history("TEST-1")
    check("fact history readable after a value change",
          len(hist) == 1 and hist[0]["old_value"] == "SI First", str(hist))

    # (4b) confidence guard: low-confidence names don't reach workflows
    mem.upsert_fact(CaseFact(case_id="FIR 302-26", type=FactType.WITNESS_NAME,
                             value="Junk Extraction", confidence=0.2))
    ctx = {"case_id": "FIR 302-26"}
    engine2 = WorkflowEngine(llm, mem, notifications.append, resolver)
    engine2.load_from_file("config/workflows.json")
    res = engine2.run("notice_draft", ctx)
    check("confidence < 0.5 witness excluded from notices",
          res.ok and res.context.get("witness_count") == 2,
          f"count={res.context.get('witness_count')}")

    # (5) file_index reconciliation
    mem.mark_processed("sample_data/DOES_NOT_EXIST.txt", 123, "TEST-1")
    for path in mem.indexed_paths():
        if not os.path.exists(path):
            mem.remove_indexed_path(path)
    check("phantom file_index entries pruned",
          "sample_data/DOES_NOT_EXIST.txt" not in mem.indexed_paths())

    print("6. Manual-trigger isp_letter is no longer blocked at read_document")
    res = engine2.run("isp_letter", {"case_id": "FIR 214-26"})
    # Without Ollama the LLM step fails — but it must get PAST read_document,
    # which was the P3.2 blocker.
    got_past = "[Read FIR copy] done" in res.log and "[Extract identifiers from FIR] start" in res.log
    check("read_document resolves the FIR copy by pattern", got_past, str(res.log))

    mem.close()
    for f in (DB, DB + "-shm", DB + "-wal"):
        if os.path.exists(f):
            os.remove(f)

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("RESULT: all checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
