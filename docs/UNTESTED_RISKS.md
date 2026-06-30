# Pathfinder Agent — Untested Failure Scenarios (run in order)

What's **verified** so far (offline smoke test): module imports, date parsing,
SQLite memory + versioning, structural folder scan, `.txt` extraction, the
offline workflow path (generate_doc/write_file/notify), and panel rendering.

Everything below needs an **external service, an optional dependency, or real
data**, so it's untested. Work top-down — each tier assumes the ones above pass.

Legend: **Test** = how to exercise it · **Expect** = pass criteria · **If it breaks** = likely cause.

---

## P0 — Environment & boot (blockers; nothing else works until these pass)

### P0.1 — Optional dependencies install
- **Test:** `pip install -r agent/requirements.txt`
- **Expect:** clean install of fastapi, uvicorn, ollama, python-docx, pypdf, watchdog, mcp.
- **If it breaks:** version pins vs Python 3.9 (mcp SDK may need 3.10+ — if so, drop it; we have our own `mcp_client.py`).

### P0.2 — Ollama reachable + model pulled
- **Test:** `ollama serve` running; `ollama pull qwen2.5:3b`; then `curl http://localhost:11434/api/tags`.
- **Expect:** model listed; `OllamaAdapter.is_available()` returns True.
- **If it breaks:** wrong `base_url`/model in `config/pathfinder.json`; model not pulled.

### P0.3 — `run` command boots all threads + IPC server
- **Test:** set `watched_folders` to a sample dir, `python -m pathfinder_agent run`.
- **Expect:** "serving on http://127.0.0.1:8765"; no thread crash in console.
- **If it breaks:** uvicorn blocking vs daemon-thread interaction; import error in ipc.

### P0.4 — IPC endpoints respond
- **Test:** `curl http://127.0.0.1:8765/health` and `/panels`.
- **Expect:** JSON status + panel arrays.
- **If it breaks:** FastAPI not installed (P0.1); loop not passed to app.

### P0.5 — Clean shutdown
- **Test:** Ctrl-C the `run` process.
- **Expect:** threads join within 5s, DB closes, no hang.
- **If it breaks:** worker blocked on `queue.get()` — sentinel `None` not delivered; uvicorn signal handling.

---

## P1 — LLM extraction correctness (the core value; highest-risk logic)

### P1.1 — Real 3B model returns parseable JSON ⚠ highest risk
- **Test:** ingest one `.txt` FIR with the model up; inspect `facts` table.
- **Expect:** ≥ a few typed facts stored.
- **If it breaks:** model wraps JSON in ```json fences, adds prose, emits an object
  not an array, or trailing commas. Our parser takes first `[` … last `]` —
  harden `_parse` (strip fences, retry, tolerate object) if this fails.

### P1.2 — `event_date` normalization from the model
- **Test:** FIR text with a court date; check `event_date_ms > 0` on that fact.
- **Expect:** deadlines/court dates get epoch dates; appear in `/panels` major_updates.
- **If it breaks:** model returns DD/MM/YYYY instead of ISO despite the prompt —
  add a server-side date fallback parser (we already have `filename_date_to_ms`).

### P1.3 — `.docx` extraction
- **Test:** drop a real `.docx` case diary in a watched folder.
- **Expect:** `extract_text` returns paragraphs; facts stored.
- **If it breaks:** python-docx missing (P0.1); tables/headers not captured (we only
  read `paragraphs` — may need table extraction).

### P1.4 — `.pdf` extraction (text-layer PDFs)
- **Test:** a digitally-generated PDF FIR.
- **Expect:** non-empty text; facts stored.
- **If it breaks:** pypdf returns empty for some encodings → see P4.1 (scanned PDFs).

### P1.5 — End-to-end ingest of one real FIR folder
- **Test:** point `watched_folders` at a copy of a real `FIR xxx-yy` folder; `run`.
- **Expect:** case appears in `/panels` with structural facts (accused, dated events)
  AND LLM facts (sections, parties). Chronology ordered by real dates.
- **If it breaks:** any of P1.1–P1.4; convention mismatch (P4.3).

### P1.6 — Chunking latency on RTX 3050
- **Test:** a multi-page document (6 chunks × chat calls).
- **Expect:** completes without timeout; acceptable wall-time.
- **If it breaks:** 60s timeout too low, or 6 sequential calls too slow — reduce
  `max_chunks`, raise `timeout_s`, or extract structurally first and LLM only the rest.

---

## P2 — Concurrency & runtime robustness

### P2.1 — Threaded SQLite (worker writes + IPC reads)
- **Test:** hit `/panels` repeatedly while files are being ingested.
- **Expect:** no "database is locked" / "recursive use" errors.
- **If it breaks:** single shared connection across threads — our lock should cover
  it, but verify; if not, switch to per-thread connections or a write queue.

### P2.2 — Watcher detects create AND modify
- **Test:** add a new file, then edit it; watch the worker pick up both.
- **Expect:** two ingests; modified file re-extracted.
- **If it breaks:** mtime second-resolution collision (edit within same second);
  `os.path.getmtime` rounding.

### P2.3 — Worker survives a bad file
- **Test:** drop a corrupt/locked/zero-byte docx.
- **Expect:** error logged, worker keeps running, file marked processed (no retry storm).
- **If it breaks:** unhandled exception kills the worker thread.

### P2.4 — Large folder polling cost
- **Test:** watch a folder with hundreds of files; observe CPU each 5s poll.
- **Expect:** negligible CPU.
- **If it breaks:** `os.walk` over a big tree every interval — switch to `watchdog`
  events (already a dependency) instead of polling.

---

## P3 — MCP & workflows

### P3.1 — MCP filesystem server connects (Windows)
- **Test:** `McpClient("npx", ["-y","@modelcontextprotocol/server-filesystem","."]).connect()`.
- **Expect:** handshake succeeds; `list_tools()` non-empty.
- **If it breaks:** `shell=True` quoting; npx not on PATH; **stdio framing** — some
  servers use Content-Length headers, our client assumes newline-delimited JSON.

### P3.2 — Workflow with real LLM (UC2 ISP letter)
- **Test:** `POST /workflow/isp_letter/run?case_id=<id>` with model up.
- **Expect:** `ok:true`, a draft in `context`, notification recorded.
- **If it breaks:** `read_document` needs a `path` (manual trigger has none) — pass
  one in config or seed `trigger_path`.

### P3.3 — Deadline-triggered workflows fire
- **Test:** a case with a court date within 14 days; wait for the 30-min tick (or call
  `dispatch_deadline_check`).
- **Expect:** UC3/UC5 workflows run, notifications appear at `/notifications`.
- **If it breaks:** no deadline facts have `event_date_ms` set (depends on P1.2).

### P3.4 — IPC path with spaces in case_id
- **Test:** `GET /panels/chronology/FIR%20201-25`.
- **Expect:** correct chronology returned.
- **If it breaks:** URL-encoding of `FIR 201-25`; route matching with spaces/slashes —
  may need a query param instead of a path segment.

---

## P4 — Real-world data edge cases

### P4.1 — Scanned (image-only) PDFs ⚠ likely in real data
- **Test:** a scanned FIR PDF (no text layer).
- **Expect:** currently yields empty text → no LLM facts (structural pass still works).
- **If it breaks (i.e. silent empties):** needs OCR (Tesseract / `pytesseract`) —
  add an OCR fallback when pypdf text is empty.

### P4.2 — Hindi / Devanagari content
- **Test:** a Hindi case diary.
- **Expect:** UTF-8 text extracted; model handles it (or IndicTrans pre-step).
- **If it breaks:** encoding (`errors="replace"` may mangle); 3B model weak on Hindi —
  this is where Sarvam/IndicTrans2 comes in.

### P4.3 — Folder convention drift
- **Test:** real folders whose names/structure differ from `FIR 201-25` + suspect-subfolders.
- **Expect:** `looks_like_case_folder` and `_FIR_RE` still match.
- **If it breaks:** tune the regexes/conventions in `analyzer.py` to the real layout.

### P4.4 — Files directly in the watched root (not inside a case folder)
- **Test:** a loose `.txt` in the watched dir.
- **Expect:** sensible `case_id` (currently = watched-folder name) or skip.
- **If it breaks:** decide policy — ignore loose files, or bucket as "uncategorized".

---

## Suggested run order
P0.1 → P0.5 (boot), then P1.1 → P1.5 (the core), then P2 (robustness),
then P3 (MCP/workflows), then P4 (real data). Stop and fix at the first P0/P1
failure — later tiers depend on them.
