"""AgentLoop — orchestrates the whole backend.

Threads:
  - watcher: polls watched folders, enqueues changed files
  - worker:  drains the queue (text extract + LLM facts + workflow dispatch)
  - tick:    periodic deadline workflows, fact eviction, panel-change signal

The file watcher only enqueues, so detection never blocks on the LLM.
Reads go to PanelDataStore via on-demand computation from memory.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Dict, List, Optional

from . import analyzer, panels
from .config import Config
from .extraction import CaseExtractor
from .llm import make_llm_adapter
from .memory import AgentMemory
from .pipeline import DocumentPipeline
from .workflow import WorkflowEngine, WorkflowResult


def _mtime_ms(path: str) -> int:
    try:
        return int(os.path.getmtime(path) * 1000)
    except OSError:
        return 0


class AgentLoop:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._mem = AgentMemory(cfg.memory.db_path)
        self._llm = make_llm_adapter(cfg.llm)
        self._extractor = CaseExtractor(self._llm)
        self._pipeline = DocumentPipeline(self._mem, self._extractor, cfg.agent)
        self._notifications: List[Dict] = []
        self._notif_lock = threading.Lock()
        self._wf = WorkflowEngine(self._llm, self._mem, self._on_notify)

        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._running = False
        self._threads: List[threading.Thread] = []
        self._llm_ok = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self, workflows_path: str = "config/workflows.json") -> None:
        self._wf.load_from_file(workflows_path)
        self._llm_ok = self._llm.is_available()
        if not self._llm_ok:
            print(f"[agent] WARNING: LLM unavailable at {self._cfg.llm.base_url} "
                  f"({self._cfg.llm.model}). Structural facts only until it's up.")

        self._running = True

        # Initial structural pass (no LLM) + enqueue files for LLM extraction.
        for folder in self._cfg.agent.watched_folders:
            analyzer.scan_and_ingest(self._mem, folder)
            self._enqueue_existing(folder)

        self._spawn(self._worker_loop)
        self._spawn(self._watcher_loop)
        self._spawn(self._tick_loop)

    def stop(self) -> None:
        self._running = False
        self._jobs.put(None)  # unblock worker
        for t in self._threads:
            t.join(timeout=5)
        self._mem.close()

    def _spawn(self, target) -> None:
        t = threading.Thread(target=target, daemon=True)
        t.start()
        self._threads.append(t)

    # ── watcher / worker ──────────────────────────────────────────────────────
    def _enqueue_existing(self, folder: str) -> None:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext in self._cfg.agent.supported_extensions:
                    p = os.path.join(root, name)
                    self._jobs.put((p, _mtime_ms(p), "created"))

    def _watcher_loop(self) -> None:
        snapshot: Dict[str, int] = {}
        while self._running:
            time.sleep(self._cfg.agent.poll_interval_s)
            current: Dict[str, int] = {}
            for folder in self._cfg.agent.watched_folders:
                for root, _dirs, files in os.walk(folder):
                    for name in files:
                        ext = os.path.splitext(name)[1].lower()
                        if ext in self._cfg.agent.supported_extensions:
                            p = os.path.join(root, name)
                            current[p] = _mtime_ms(p)
            for p, m in current.items():
                if p not in snapshot:
                    self._jobs.put((p, m, "created"))
                elif snapshot[p] != m:
                    self._jobs.put((p, m, "modified"))
            snapshot = current

    def _worker_loop(self) -> None:
        while self._running:
            job = self._jobs.get()
            if job is None:
                break
            path, mtime, event = job
            try:
                if self._llm_ok:
                    self._pipeline.process_file(path, mtime)
                self._wf.dispatch_file_event(path, event)
            except Exception as e:  # noqa: BLE001 keep the worker alive
                print(f"[agent] worker error on {path}: {e}")

    def _tick_loop(self) -> None:
        last_deadline = 0.0
        while self._running:
            time.sleep(self._cfg.agent.poll_interval_s)
            now = time.time()
            if now - last_deadline >= 1800:  # 30 min
                try:
                    self._wf.dispatch_deadline_check()
                    self._mem.evict_old_facts(self._cfg.memory.fact_ttl_days)
                except Exception as e:  # noqa: BLE001
                    print(f"[agent] tick error: {e}")
                last_deadline = now

    # ── notifications ─────────────────────────────────────────────────────────
    def _on_notify(self, payload: Dict) -> None:
        with self._notif_lock:
            self._notifications.insert(0, payload)
            del self._notifications[50:]

    def notifications(self) -> List[Dict]:
        with self._notif_lock:
            return list(self._notifications)

    # ── read API (used by IPC) ────────────────────────────────────────────────
    @property
    def memory(self) -> AgentMemory:
        return self._mem

    def get_panels(self) -> Dict:
        return panels.all_panels(self._mem)

    def get_chronology(self, case_id: str) -> List[Dict]:
        return panels.chronology(self._mem, case_id)

    def run_workflow(self, workflow_id: str, case_id: str = "") -> WorkflowResult:
        ctx = {"case_id": case_id} if case_id else {}
        return self._wf.run(workflow_id, ctx)

    def status(self) -> Dict:
        return {
            "running": self._running,
            "llm": self._llm.name,
            "llm_available": self._llm_ok,
            "watched_folders": self._cfg.agent.watched_folders,
            "workflows": [w.get("id") for w in self._wf.list_workflows()],
        }
