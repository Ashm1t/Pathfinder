"""WorkflowEngine — composable, JSON-defined task pipelines.

Ported from the C++ WorkflowEngine. A workflow is a trigger + ordered steps;
each step reads/writes a shared context dict. Steps run outside any lock so the
LLM never blocks registration/dispatch.

Step types: read_document, extract_facts, llm_transform, generate_doc,
            write_file, notify.
Triggers:   manual, file_created, file_modified, deadline.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .extraction import CaseExtractor, extract_text
from .llm import LlmAdapter
from .memory import AgentMemory
from .models import FactType


@dataclass
class WorkflowResult:
    ok: bool = False
    error: str = ""
    context: Dict = field(default_factory=dict)
    log: List[str] = field(default_factory=list)


def _substitute(template: str, data: Dict) -> str:
    out = template
    for k, v in data.items():
        out = out.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v))
    return out


class WorkflowEngine:
    def __init__(self, llm: LlmAdapter, memory: AgentMemory,
                 notify_cb: Optional[Callable[[Dict], None]] = None):
        self._llm = llm
        self._mem = memory
        self._notify = notify_cb
        self._workflows: List[Dict] = []
        self._lock = threading.Lock()

    def load_from_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                defs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        with self._lock:
            self._workflows = [w for w in defs if isinstance(w, dict)]

    def list_workflows(self) -> List[Dict]:
        with self._lock:
            return list(self._workflows)

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
        deadlines = self._mem.get_upcoming_deadlines(14)
        if not deadlines:
            return
        matched = []
        with self._lock:
            for w in self._workflows:
                if w.get("enabled", True) and \
                   w.get("trigger", {}).get("type") == "deadline":
                    matched.append(w)
        for w in matched:
            for d in deadlines:
                self._execute(w, {
                    "case_id": d.case_id,
                    "deadline_type": d.type.value,
                    "deadline_value": d.value,
                })

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
        if stype == "read_document":
            path = cfg.get("path") or ctx.get("trigger_path", "")
            if not path:
                return False
            text, err = extract_text(path)
            if text is None:
                return False
            ctx["document_text"] = text
            ctx["document_path"] = path
            return True
        if stype == "extract_facts":
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
        if stype == "llm_transform":
            user = _substitute(cfg.get("user_template", ""), ctx)
            resp = self._llm.chat(
                [{"role": "system", "content": cfg.get("system_prompt", "")},
                 {"role": "user", "content": user}],
                temperature=cfg.get("temperature", 0.2),
                max_tokens=cfg.get("max_tokens", 1024))
            if not resp.ok:
                return False
            ctx[cfg.get("output_key", "llm_output")] = resp.content
            return True
        if stype == "generate_doc":
            tmpl = _substitute(cfg.get("template", ""), ctx)
            case_id = ctx.get("case_id", "")
            if case_id:
                for f in self._mem.get_all_facts(case_id):
                    tmpl = tmpl.replace("{{" + f.type.value + "}}", f.value)
            ctx[cfg.get("output_key", "generated_doc")] = tmpl
            return True
        if stype == "write_file":
            out_path = cfg.get("output_path", "")
            content = ctx.get(cfg.get("content_key", "generated_doc"), "")
            if not out_path or not content:
                return False
            try:
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return True
            except OSError:
                return False
        if stype == "notify":
            payload = {
                "message": cfg.get("message", "Workflow completed"),
                "severity": cfg.get("severity", "info"),
                "case_id": ctx.get("case_id", ""),
            }
            ctx["notification"] = payload
            if self._notify:
                self._notify(payload)
            return True
        return False
