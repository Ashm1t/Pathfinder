"""WorkflowEngine — executes blueprint workflows (see workflow_schema.py).

A workflow is a trigger + ordered steps; each step reads/writes a shared
context dict. Steps run outside any lock so the LLM never blocks
registration/dispatch. Every workflow is draft-only: outputs land in a
drafts folder and end with a notify — nothing is filed or sent.

Step types: gather_case, read_document, extract_facts, checklist,
            llm_transform, for_each, generate_doc, write_file, notify.
Triggers:   manual, file_created, file_modified, deadline
            (deadline honours trigger.config.days_before and .type).

Workflows load from multiple JSON files (hand-authored + LLM-generated);
register() validates against the blueprint and persists new ones.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .extraction import CaseExtractor, extract_text
from .llm import LlmAdapter
from .mcp_client import McpClient
from .memory import AgentMemory
from .models import FactType, now_ms
from .workflow_schema import validate_workflow


@dataclass
class WorkflowResult:
    ok: bool = False
    error: str = ""
    context: Dict = field(default_factory=dict)
    log: List[str] = field(default_factory=list)


def _render_value(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        # Lists read as document bullet lines, not JSON.
        return "\n".join(f"- {x}" for x in v) if v else "(none on record)"
    return json.dumps(v)


def _substitute(template: str, data: Dict) -> str:
    out = template
    for k, v in data.items():
        out = out.replace("{{" + k + "}}", _render_value(v))
    return out


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d.%m.%Y")


class WorkflowEngine:
    def __init__(self, llm: LlmAdapter, memory: AgentMemory,
                 notify_cb: Optional[Callable[[Dict], None]] = None,
                 case_files_resolver: Optional[Callable[[str], List[str]]] = None,
                 mcp_servers: Optional[Dict[str, Dict]] = None):
        self._llm = llm
        self._mem = memory
        self._notify = notify_cb
        self._resolve_case_files = case_files_resolver or (lambda case_id: [])
        self._mcp_servers = mcp_servers or {}
        self._mcp_clients: Dict[str, McpClient] = {}
        self._workflows: List[Dict] = []
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        for client in self._mcp_clients.values():
            client.close()
        self._mcp_clients.clear()

    def _get_mcp(self, server: str) -> Optional[McpClient]:
        """Lazily spawn + handshake a configured MCP server, cached."""
        if server in self._mcp_clients:
            return self._mcp_clients[server]
        spec = self._mcp_servers.get(server)
        if not spec or not spec.get("command"):
            return None
        client = McpClient(spec["command"], list(spec.get("args", [])))
        try:
            if not client.connect():
                return None
        except OSError:
            return None
        self._mcp_clients[server] = client
        return client

    # ── loading / registration ────────────────────────────────────────────────
    def load_from_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                defs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        with self._lock:
            existing = {w.get("id") for w in self._workflows}
            for w in defs:
                if isinstance(w, dict) and w.get("id") not in existing:
                    self._workflows.append(w)
                    existing.add(w.get("id"))

    def load_paths(self, paths: List[str]) -> None:
        for p in paths:
            self.load_from_file(p)

    def list_workflows(self) -> List[Dict]:
        with self._lock:
            return list(self._workflows)

    def known_ids(self) -> List[str]:
        with self._lock:
            return [w.get("id", "") for w in self._workflows]

    def register(self, wf: Dict, persist_path: str = "") -> List[str]:
        """Validate against the blueprint; on success add (and persist) it.

        Returns validation errors; empty list means registered.
        """
        errors = validate_workflow(wf, existing_ids=set(self.known_ids()))
        if errors:
            return errors
        with self._lock:
            self._workflows.append(wf)
        if persist_path:
            try:
                existing = []
                if os.path.exists(persist_path):
                    with open(persist_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                existing.append(wf)
                os.makedirs(os.path.dirname(persist_path) or ".", exist_ok=True)
                with open(persist_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
            except (OSError, json.JSONDecodeError) as e:
                return [f"registered in memory but failed to persist: {e}"]
        return []

    # ── dispatch ──────────────────────────────────────────────────────────────
    def run(self, workflow_id: str, context: Optional[Dict] = None) -> WorkflowResult:
        wf = None
        with self._lock:
            for w in self._workflows:
                if w.get("id") == workflow_id and w.get("enabled", True):
                    wf = w
                    break
        if wf is None:
            return WorkflowResult(False, f"Workflow not found: {workflow_id}")
        ctx = dict(context or {})
        ctx["workflow_id"] = workflow_id
        return self._execute(wf, ctx)

    def dispatch_file_event(self, path: str, event: str) -> None:
        matched = []
        with self._lock:
            for w in self._workflows:
                if not w.get("enabled", True):
                    continue
                t = w.get("trigger", {})
                if (event == "created" and t.get("type") == "file_created") or \
                   (event == "modified" and t.get("type") == "file_modified"):
                    glob = t.get("config", {}).get("glob", "")
                    if glob and glob not in path:
                        continue
                    matched.append(w)
        for w in matched:
            self._execute(w, {"trigger_path": path})

    def dispatch_deadline_check(self) -> None:
        """Fire deadline-triggered workflows, honouring each workflow's own
        trigger.config: days_before (window) and type (fact type filter)."""
        deadlines = self._mem.get_upcoming_deadlines(30)
        if not deadlines:
            return
        with self._lock:
            matched = [w for w in self._workflows
                       if w.get("enabled", True)
                       and w.get("trigger", {}).get("type") == "deadline"]
        now = now_ms()
        for w in matched:
            tcfg = w.get("trigger", {}).get("config", {})
            days = float(tcfg.get("days_before", 14))
            want_type = tcfg.get("type", "")
            for d in deadlines:
                if want_type and d.type.value != want_type:
                    continue
                if d.event_date_ms - now > days * 86400 * 1000:
                    continue
                # Fire once per (workflow, deadline) — not on every tick.
                wf_id = w.get("id", "")
                if self._mem.was_deadline_dispatched(
                        wf_id, d.case_id, d.type.value, d.event_date_ms):
                    continue
                res = self._execute(w, {
                    "case_id": d.case_id,
                    "deadline_type": d.type.value,
                    "deadline_value": d.value,
                })
                if res.ok:
                    self._mem.mark_deadline_dispatched(
                        wf_id, d.case_id, d.type.value, d.event_date_ms)

    # ── execution ─────────────────────────────────────────────────────────────
    def _execute(self, wf: Dict, ctx: Dict) -> WorkflowResult:
        res = WorkflowResult(ok=True, context=ctx)
        for step in wf.get("steps", []):
            name = step.get("name", step.get("type", "?"))
            res.log.append(f"[{name}] start")
            ok = self._run_step(step, ctx)
            if not ok:
                res.ok = False
                res.error = f"Step failed: {name}"
                res.context = ctx
                return res
            res.log.append(f"[{name}] done")
        res.context = ctx
        return res

    def _run_step(self, step: Dict, ctx: Dict) -> bool:
        stype = step.get("type")
        cfg = step.get("config", {})
        handler = getattr(self, f"_step_{stype}", None)
        if handler is None:
            return False
        return handler(cfg, ctx)

    # ── GATHER steps ──────────────────────────────────────────────────────────
    def _step_gather_case(self, cfg: Dict, ctx: Dict) -> bool:
        case_id = ctx.get("case_id", "")
        if not case_id:
            return False
        rec = self._mem.get_case(case_id)
        ctx["case_title"] = (rec.title if rec else "") or case_id

        files = self._resolve_case_files(case_id)
        ctx["case_files"] = files

        cap = int(cfg.get("max_corpus_chars", 120_000))
        parts: List[str] = []
        used = 0
        for p in files:
            text, _err = extract_text(p)
            if not text:
                continue
            block = f"\n===== {os.path.basename(p)} =====\n{text}"
            parts.append(block[: max(0, cap - used)])
            used += len(block)
            if used >= cap:
                break
        ctx["case_corpus"] = "".join(parts)

        facts = self._mem.get_all_facts(case_id)

        def _values(ft: FactType) -> List[str]:
            # confidence guard: low-confidence extractions must never drive
            # generated legal documents (notices fan out over these lists)
            seen, out = set(), []
            for f in facts:
                if f.type == ft and f.confidence >= 0.5 and f.value not in seen:
                    seen.add(f.value)
                    out.append(f.value)
            return out

        ctx["witnesses"] = _values(FactType.WITNESS_NAME)
        ctx["accused"] = _values(FactType.ACCUSED_NAME)
        ctx["seized_property"] = _values(FactType.SEIZED_PROPERTY)

        chron = self._mem.get_chronology(case_id)
        ctx["chronology_text"] = "\n".join(
            f"- {_ms_to_date(f.event_date_ms) if f.event_date_ms else 'undated'}: "
            f"{f.value}" for f in chron) or "(no chronology on record)"
        return True

    def _step_read_document(self, cfg: Dict, ctx: Dict) -> bool:
        path = cfg.get("path") or ctx.get("trigger_path", "")
        if not path and cfg.get("pattern") and ctx.get("case_id"):
            pattern = cfg["pattern"]
            for p in self._resolve_case_files(ctx["case_id"]):
                if fnmatch.fnmatch(os.path.basename(p), pattern):
                    path = p
                    break
        if not path:
            return False
        text, _err = extract_text(path)
        if text is None:
            return False
        ctx["document_text"] = text
        ctx["document_path"] = path
        return True

    # ── DERIVE steps ──────────────────────────────────────────────────────────
    def _step_extract_facts(self, cfg: Dict, ctx: Dict) -> bool:
        text = ctx.get("document_text", "")
        if not text:
            return False
        extractor = CaseExtractor(self._llm)
        facts = extractor.extract(ctx.get("case_id", ""),
                                  ctx.get("document_path", ""), text)
        for f in facts:
            self._mem.upsert_fact(f)
        ctx["extracted_fact_count"] = len(facts)
        return True

    def _step_checklist(self, cfg: Dict, ctx: Dict) -> bool:
        src = ctx.get(cfg.get("source", "case_corpus"), "")
        if not src:
            return False
        lines: List[str] = []
        missing = 0
        for item in cfg.get("items", []):
            label = item.get("label", "?")
            try:
                ok = re.search(item.get("pattern", ""), src,
                               re.IGNORECASE) is not None
            except re.error:
                ok = False
            lines.append(f"[{'x' if ok else ' '}] {label}")
            if not ok:
                missing += 1
        ctx[cfg.get("output_key") or "checklist_report"] = "\n".join(lines)
        ctx["checklist_missing"] = missing
        return True

    def _step_llm_transform(self, cfg: Dict, ctx: Dict) -> bool:
        user = _substitute(cfg.get("user_template", ""), ctx)
        resp = self._llm.chat(
            [{"role": "system", "content": cfg.get("system_prompt", "")},
             {"role": "user", "content": user}],
            temperature=cfg.get("temperature", 0.2),
            max_tokens=cfg.get("max_tokens", 1024))
        if not resp.ok:
            return False
        ctx[cfg.get("output_key") or "llm_output"] = resp.content
        return True

    def _step_for_each(self, cfg: Dict, ctx: Dict) -> bool:
        items = ctx.get(cfg.get("over", ""), [])
        if not isinstance(items, list):
            return False
        alias = cfg.get("as", "item")
        for i, item in enumerate(items):
            sub = dict(ctx)
            sub[alias] = item
            sub[f"{alias}_index"] = str(i + 1)
            for st in cfg.get("steps", []):
                if not self._run_step(st, sub):
                    return False
        ctx[f"{alias}_count"] = len(items)
        return True

    # ── PRODUCE / DELIVER steps ───────────────────────────────────────────────
    def _step_generate_doc(self, cfg: Dict, ctx: Dict) -> bool:
        tmpl = _substitute(cfg.get("template", ""), ctx)
        case_id = ctx.get("case_id", "")
        if case_id:
            by_type: Dict[str, List[str]] = {}
            for f in self._mem.get_all_facts(case_id):
                vals = by_type.setdefault(f.type.value, [])
                if f.value not in vals:
                    vals.append(f.value)
            for name, values in by_type.items():
                joined = values[0] if len(values) == 1 \
                    else "\n".join(f"- {v}" for v in values)
                tmpl = tmpl.replace("{{" + name + "}}", joined)
        # A fact type with nothing on record must be visible to the reviewing
        # officer, not left as raw template syntax.
        for ft in FactType:
            tmpl = tmpl.replace("{{" + ft.value + "}}",
                                f"[no {ft.value} on record]")
        ctx[cfg.get("output_key") or "generated_doc"] = tmpl
        return True

    def _step_write_file(self, cfg: Dict, ctx: Dict) -> bool:
        out_path = _substitute(cfg.get("output_path", ""), ctx)
        content = ctx.get(cfg.get("content_key", "generated_doc"), "")
        if not out_path or not content:
            return False
        # Draft-only guardrail: relative paths only, no traversal.
        norm = out_path.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", out_path) or \
                ".." in norm.split("/"):
            return False
        try:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            ctx["written_path"] = out_path
            return True
        except OSError:
            return False

    def _step_mcp_call(self, cfg: Dict, ctx: Dict) -> bool:
        """Call a tool on a configured MCP server — the action side.
        String argument values get {{ctx}} substitution."""
        client = self._get_mcp(cfg.get("server", ""))
        if client is None:
            return False
        arguments = {}
        for k, v in (cfg.get("arguments") or {}).items():
            arguments[k] = _substitute(v, ctx) if isinstance(v, str) else v
        res = client.call_tool(cfg.get("tool", ""), arguments)
        if not res.get("ok"):
            return False
        result = res.get("result", {})
        # MCP tool results carry content blocks; surface the text plainly.
        blocks = result.get("content", [])
        text = "\n".join(b.get("text", "") for b in blocks
                         if isinstance(b, dict) and b.get("type") == "text")
        if result.get("isError"):
            return False
        ctx[cfg.get("output_key") or "mcp_result"] = text or json.dumps(result)
        return True

    def _step_notify(self, cfg: Dict, ctx: Dict) -> bool:
        payload = {
            "message": _substitute(cfg.get("message", "Workflow completed"), ctx),
            "severity": cfg.get("severity", "info"),
            "case_id": ctx.get("case_id", ""),
        }
        ctx["notification"] = payload
        if self._notify:
            self._notify(payload)
        return True
