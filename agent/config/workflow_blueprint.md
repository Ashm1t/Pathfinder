# Workflow blueprint

Every Pathfinder workflow is the same five-phase pipeline, extracted from the
four shipped workflows and the sample-data proving runs:

```
TRIGGER      →  GATHER            →  DERIVE              →  PRODUCE        →  DELIVER
when it runs    collect the facts    compute the content     fill templates    draft + tell
─────────────   ────────────────    ─────────────────────   ──────────────   ─────────────
manual          gather_case          checklist (regex)       generate_doc      write_file
file_created    read_document        extract_facts (LLM)     for_each          (drafts/ only)
file_modified                        llm_transform (LLM)                       notify (always
deadline                                                                        the last step)
```

**The standing rule: every output is a draft.** `write_file` may only write
relative paths (the `drafts/` folder); the last step is always `notify` so the
officer is told. There is no step that files, sends, or submits anything — by
design, and the validator enforces it.

## Anatomy of a definition

```json
{
  "id": "snake_case_id",
  "name": "Human-readable name",
  "description": "One sentence: what this drafts and when it fires",
  "enabled": true,
  "trigger": { "type": "manual | file_created | file_modified | deadline",
               "config": { "days_before": 7, "type": "ChargesheetDeadline" } },
  "steps": [ { "type": "...", "name": "...", "config": { } } ]
}
```

Steps pass data through a shared **context**. The trigger seeds it
(`manual` → `case_id`; file triggers → `trigger_path`; `deadline` →
`case_id`, `deadline_type`, `deadline_value`), each step adds to it, and
`{{key}}` placeholders in any template pull from it — or from the case's
stored **fact types** (`{{FirNumber}}`, `{{PoliceStation}}`, `{{IoName}}`,
…), which `generate_doc` resolves from memory.

## Step catalog

| Step | Phase | Needs | Provides | LLM? |
|---|---|---|---|---|
| `gather_case` | gather | `case_id` | `case_files`, `case_corpus`, `chronology_text`, `witnesses`, `accused`, `case_title` | no |
| `read_document` | gather | a `path`, `trigger_path`, or `pattern`+`case_id` | `document_text`, `document_path` | no |
| `checklist` | derive | a text source (default `case_corpus`) | report under `output_key`, `checklist_missing` | no |
| `extract_facts` | derive | `document_text` | `extracted_fact_count` (+ facts into memory) | **yes** |
| `llm_transform` | derive | whatever its `user_template` references | prose under `output_key` | **yes** |
| `for_each` | produce | a list key (`over`), binds each item to `as` | `<as>_count`; sub-steps run per item | no |
| `generate_doc` | produce | whatever its `template` references | document under `output_key` | no |
| `write_file` | deliver | `content_key` (default `generated_doc`) | `written_path` | no |
| `notify` | deliver | — | `notification` | no |

Prefer the deterministic steps: if a document can be produced by filling a
template from known facts, do that — reserve `llm_transform` for prose that
genuinely can't (the ip_letter lesson: never spend a model call on what a
template gives you for free). A workflow with no LLM steps runs even when
Ollama is down.

## Creating a new workflow

1. **By hand** — copy the scaffold (`workflow_schema.make_scaffold()` prints
   one), edit, drop it into `config/workflows.json`.
2. **On demand via the LLM** — the officer describes what they want:
   `POST /workflow/author {"request": "...", "register": false}` (or
   `python -m pathfinder_agent author "..."`). The model writes a definition
   against this blueprint; `validate_workflow()` statically checks it —
   dataflow (every step's inputs satisfied by the trigger or an earlier
   step), template placeholders, config fields, draft-only rules — and
   feeds any errors back for one self-repair round. Only a definition that
   validates can be registered; registered definitions persist to
   `config/workflows.generated.json`.

The validator is the safety boundary: **the LLM proposes, the schema
disposes.** Authored workflows are still made of the same audited step
types — the model can compose building blocks, not invent new powers.

## Composition — building blocks for bigger agent designs

Because every workflow reads and writes the same context and memory, larger
behaviours compose from small ones: a `file_created`-triggered workflow that
extracts facts feeds the same memory that a `deadline`-triggered reporting
workflow reads; `for_each` fans a produce/deliver pair over any gathered
list. New capabilities should arrive as new *step types* (each with a
`needs`/`provides` contract in `workflow_schema.STEP_SPECS`) — e.g. a future
`mcp_call` step — which every existing and future workflow can then use.
