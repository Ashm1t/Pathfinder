# Sample data — 3 simulated FIR cases

Purpose-built to exercise every fact type, panel, and workflow the backend
promises, while staying inside the analyzer's documented folder convention
(`<watched folder>/FIR <num>-<yy>/<accused name>/` + loose dated files in the
case-folder root — see `pathfinder_agent/analyzer.py` module docstring).

All content is fictional. Dates are anchored to **2026-07-03** ("today" at
the time this data was written) — see "Refreshing the dates" below.

`agent/config/pathfinder.json` → `agent.watched_folders` now points at
`"sample_data"` (this directory, as the *parent* of the case folders — the
analyzer walks one level down from each watched-folder entry looking for
`FIR *` children, so the entry must be the parent, not a case folder itself).

## The three cases

### FIR 214-26 — online investment fraud (hero case, richest data)
PS Cyber Crime, Gurugram · 3 accused (Rohit Bansal, Sunita Kadam, Deepak
Wahi) · victim Neha Kapoor defrauded of ₹18.4L via a fake trading app.
12 documents: FIR copy, 3 interrogation/disclosure statements, 3 case-diary
entries, a seizure memo, a Section 91 BNSS notice to an ISP + its reply, a
chargesheet status note, and a court notice.

- **Chargesheet deadline 08.07.2026** — 5 days from the "today" anchor →
  lands inside both the 7-day `whats_next` window and the 14-day
  `major_updates` window (shows as URGENT).
- **Court date 05.07.2026** — 2 days from anchor → inside the 3-day
  `court_compliance` trigger window too.
- Exercises **all 19** `FactType` members on its own (see mapping below).

### FIR 189-26 — chain snatching (quiet case, no near-term deadline)
PS Sector 29, Faridabad · 2 accused (Sonu Yadav, Imran Sheikh) · victim
Kavita Rani. 6 documents: FIR copy, 2 interrogation/disclosure statements,
2 case-diary entries, a recovery memo. Deliberately has **no** stated
chargesheet/court date, so it sits quietly in `recent_cases` without
triggering `major_updates`/`whats_next` — demonstrates a case that isn't
screaming for attention, for contrast against FIR 214-26.

### FIR 302-26 — cheque bounce / criminal breach of trust
PS Sadar Bazar, Panipat · 1 accused (Manoj Tiwari) · complainant Ashok
Mehra. 7 documents: FIR copy, 1 interrogation note, 2 case-diary entries,
2 witness statements, a chargesheet status note.

- **Chargesheet deadline 13.07.2026** — 10 days from anchor → inside the
  14-day `major_updates` window but **outside** the 7-day `whats_next`
  window. This is deliberate: it's the one case that proves the panel
  logic's two different lookback windows (`panels.major_updates` uses 14
  days, `panels.whats_next` uses 7) actually behave differently, not just
  in code but in what a demo audience sees on screen.

## Fact-type coverage (all 19 `FactType` members, ≥1 real instance)

| FactType | Where |
|---|---|
| CaseTitle | Every FIR copy narrative |
| FirNumber | Folder name (structural) + every FIR copy |
| PoliceStation | Every FIR copy |
| District | Every FIR copy |
| DateOfIncident | Every FIR copy |
| DateOfFIR | Every FIR copy |
| AccusedName | Empty accused subfolders (structural) + every FIR copy/interrogation note |
| AccusedAddress | Every interrogation/disclosure statement |
| WitnessName | FIR 189-26 (Ramesh Chand), FIR 302-26 (Suresh Kalra, Anita Deshmukh) |
| VictimName | Every FIR copy |
| IpcSection | Every FIR copy (BNS/BNSS/IT Act/NI Act sections, see below) |
| ChargesheetDeadline | FIR 214-26 (08.07.2026), FIR 302-26 (13.07.2026) |
| CourtDate | FIR 214-26 (05.07.2026) |
| IoName | Every FIR copy |
| CaseStatus | Every FIR copy + chargesheet status notes |
| NoticeIssued | FIR 214-26 (Section 91 BNSS notice to ISP) |
| NoticeResponse | FIR 214-26 (ISP reply with CDR records) |
| SeizedProperty | FIR 214-26 (phones/laptop/bank freeze), FIR 189-26 (recovered chain) |
| KeyEvent | Every case-diary entry + every dated filename (structural) |

Structural facts (`FirNumber`, `AccusedName`, `KeyEvent`) populate with
**zero LLM calls** — visible immediately after `scan_and_ingest`, even with
Ollama down. Everything else (deadlines, sections, addresses, notices,
seized property) requires a working LLM extraction pass — this is
deliberate, so the demo also proves the structural-vs-LLM split described in
`analyzer.py`'s docstring.

## Legal references used (verified, not invented)

BNS/BNSS replaced IPC/CrPC in July 2024; sections below are current:
- **BNS 318** — cheating (was IPC 420)
- **BNS 316** — criminal breach of trust (was IPC 405–409)
- **BNS 61** — criminal conspiracy (was IPC 120A/120B)
- **BNS 304** — snatching (new offence, no direct IPC equivalent)
- **BNS 3(5)** — acts by several persons in furtherance of common intention (was IPC 34)
- **BNSS 193** — chargesheet/final report filing timeline (replaces CrPC 173)
- **BNSS 91** — production of documents (already referenced in `config/workflows.json`'s `isp_letter` workflow)
- **BNSS 179** — attendance of witnesses (already referenced in `notice_draft`)
- **IT Act 66C/66D** — identity theft / cheating by personation (unchanged by the BNS reform)
- **NI Act 138** — dishonour of cheque (unchanged, separate statute)

## Refreshing the dates

The demo's deadline/court-date urgency (what shows as "URGENT" in Major
Updates, what appears in What's Next) is anchored to 2026-07-03. If you're
running this demo well after that date, the deadlines will read as overdue
or will have aged out of the 7/14-day panel windows. To refresh: bump every
date in `Chargesheet Status Note*.txt` and `Court Notice*.txt` (FIR 214-26,
FIR 302-26) forward by the same offset, keeping the 5-day/2-day/10-day
spacing from "today" described above.

> **2026-07-03 update:** blockers 0–2 below are all **fixed** by the workflow
> blueprint rework (see `config/workflow_blueprint.md` and
> `agent/verify_workflows.py`, which proves each fix against this data):
> KeyEvent facts are now keyed per document, `read_document` resolves case
> files by pattern, and deadline dispatch honours each workflow's
> `days_before`/`type`. The section is kept as a record of what the data
> surfaced. The deterministic runner below likewise predates the rework —
> the real engine now runs these workflows natively
> (`python -m pathfinder_agent workflow notice_draft "FIR 302-26"`).

## Known pre-existing blockers (originally not fixed — data-only pass)

Found while building this data and **confirmed by actually running the
structural scan** against it (`python -m pathfinder_agent scan sample_data`
— see the verification below). Not something more/different files can work
around; both are code-level, not data-level.

0. **Chronology silently collapses to one event per case — confirmed live.**
   `analyzer.analyze_case_folder` creates every `KeyEvent` fact with
   `key=""` (no distinguishing key is ever assigned per event).
   `AgentMemory.upsert_fact` treats `(case_id, fact_type, key)` as the
   identity for versioning — so every dated file after the first one
   *overwrites* the same row instead of adding a new chronology entry.
   Running the scan against this data proved it: FIR 214-26 has 6 dated
   documents (3 case-diary entries + seizure memo + court notice + ISP
   reply, all structurally dated), FIR 302-26 has 5, FIR 189-26 has 4 — but
   `SELECT COUNT(*) FROM facts WHERE fact_type='KeyEvent'` returns exactly
   **3** (one per case, whichever file sorts last alphabetically in
   `os.listdir`, not whichever is chronologically latest). Contrast this
   with `AccusedName`, which correctly accumulates (6 rows for 3+2+1
   accused across the three cases) because `analyzer.py` *does* give it a
   distinguishing `key` (`f"suspect_{suspect_idx}"`) — `KeyEvent` just never
   got the same treatment. This is the single biggest blocker to the
   Chronology panel actually working as designed; the fix is narrow (assign
   each `KeyEvent` a distinguishing `key`, e.g. the source filename) but
   it's a code change, not something I applied in this data-only pass —
   flagging it for your call.

1. **`isp_letter` and `notice_draft` cannot complete via manual trigger.**
   Their first step is `read_document` with an empty `config.path`
   (`config/workflows.json`), and `WorkflowEngine._run_step` falls back to
   `ctx.get("trigger_path", "")` — which a manual `POST
   /workflow/{id}/run?case_id=...` call never sets (`AgentLoop.run_workflow`
   only seeds `case_id`/`workflow_id`). The step returns `False` and the
   whole workflow fails immediately. Already flagged in
   `docs/UNTESTED_RISKS.md` P3.2. To actually run these two workflows
   end-to-end you'd need either a `path` in the step config (works for one
   fixed case only) or a small code change so manual invocation can resolve
   "the FIR copy for this case_id" — happy to make that change if you want
   it as a fast follow.
2. **`dispatch_deadline_check` ignores each workflow's `days_before`/`type`
   trigger config** (`workflow.py` matches only on `trigger.type ==
   "deadline"`, nothing narrower) — so `chargesheet_check` and
   `court_compliance` both fire on *any* upcoming `ChargesheetDeadline` or
   `CourtDate` within 14 days, not just the ones their own config implies.
   Not a data problem, just don't be surprised both workflows fire for both
   FIR 214-26's chargesheet deadline *and* its court date.

## Working runs of the other 3 workflows (no LLM needed)

`run_deterministic_workflows.py` in this folder is a standalone demonstration
of `chargesheet_check`, `notice_draft`, and `court_compliance` — actually
executed against the case data above, no Ollama required. It borrows the
idea from the reference `ip_letter` repo (github.com/saiesh2401/ip_letter):
its real ISP-notice pipeline has no LLM in the core mechanic at all — it
parses structured input and fills each ISP's own template. Applied here:

```
cd agent
python sample_data/run_deterministic_workflows.py
```

Reads the real case files with the same `extract_text()` the production
pipeline uses, and fills the same `{{placeholder}}` templates already
defined in `config/workflows.json`. Output lands in `sample_data/_generated/`
(gitignored-worthy scratch output, not case data):

- **`chargesheet_check`** — a completeness checklist per case, scanned
  against what's actually on file. Genuinely finds real gaps baked into the
  data: FIR 214-26 is missing a witness statement and has its FSL forensic
  report explicitly flagged PENDING ("is awaited" in the source text); FIR
  189-26 is missing both a witness statement and a stated deadline; FIR
  302-26 is missing a seizure memo (correctly — it's a cheque-bounce case,
  nothing to seize).
- **`notice_draft`** — one Section 179 BNSS witness notice per witness
  actually found in the documents: 1 for FIR 189-26 (Ramesh Chand, extracted
  from the FIR narrative), 2 for FIR 302-26 (Suresh Kalra, Anita Deshmukh,
  extracted from their witness-statement headers). Correctly drafts nothing
  for FIR 214-26, which has no named witnesses.
- **`court_compliance`** — a real compliance report for FIR 214-26 (the only
  case with a `CourtDate`), assembled by dating and headlining its 3 case-diary
  entries in order and filling the workflow's own `generate_doc` template.

This is a standalone prototype, not a change to `workflow.py`'s step
executor — if this checklist/fanout/assemble pattern is wanted as first-class
step types there, that's a separate, deliberate change.

## Running it

```
cd agent
pip install -r requirements.txt
ollama pull qwen2.5:3b       # ollama serve must already be running
python -m pathfinder_agent run
```

Must be launched with cwd = `agent/` — `Config.load` and the workflow-file
path are hardcoded relative paths (`config/pathfinder.json`,
`config/workflows.json`), a pre-existing gotcha noted during the earlier
architecture audit, not something this data pass changes.
