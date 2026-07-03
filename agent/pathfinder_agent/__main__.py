"""CLI entrypoint.

Usage:
    python -m pathfinder_agent scan <folder>       # offline ingest (structural
                                                   #  + header facts) + panels
    python -m pathfinder_agent panels              # show panels from existing memory
    python -m pathfinder_agent run                 # start agent loop + IPC server
    python -m pathfinder_agent workflow <id> [case_id]
                                                   # run one workflow now
    python -m pathfinder_agent author "<request>" [--register]
                                                   # LLM-author a new workflow
"""
from __future__ import annotations

import json
import os
import sys

from .analyzer import scan_and_ingest
from .config import Config
from .memory import AgentMemory
from .panels import all_panels


def _offline_ingest(cfg: Config, mem: AgentMemory, folder: str) -> int:
    """Structural scan + zero-LLM header-fact pass over every document."""
    from .extraction import CaseExtractor
    from .llm import make_llm_adapter
    from .pipeline import DocumentPipeline

    n = scan_and_ingest(mem, folder)
    pipeline = DocumentPipeline(mem, CaseExtractor(make_llm_adapter(cfg.llm)),
                                cfg.agent)
    for root, _dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            try:
                mtime = int(os.path.getmtime(p) * 1000)
            except OSError:
                continue
            pipeline.process_file(p, mtime, use_llm=False)
    return n


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1

    cfg = Config.load("config/pathfinder.json")
    mem = AgentMemory(cfg.memory.db_path)
    cmd = argv[0]

    if cmd == "scan":
        if len(argv) < 2:
            print("scan needs a folder path")
            return 1
        folder = argv[1]
        n = _offline_ingest(cfg, mem, folder)
        print(f"Scanned {folder}: {n} case folder(s) ingested "
              f"(structural + header facts, no LLM).\n")
        print(json.dumps(all_panels(mem), indent=2, ensure_ascii=False))
        return 0

    if cmd == "panels":
        print(json.dumps(all_panels(mem), indent=2, ensure_ascii=False))
        return 0

    if cmd == "workflow":
        if len(argv) < 2:
            print("workflow needs a workflow id (and usually a case_id)")
            return 1
        mem.close()
        from .agent_loop import AgentLoop
        loop = AgentLoop(cfg)
        loop.load_workflows()
        res = loop.run_workflow(argv[1], argv[2] if len(argv) > 2 else "")
        print("\n".join(res.log))
        if res.ok:
            print(f"\nOK — written: {res.context.get('written_path', '(none)')}")
            notif = res.context.get("notification")
            if notif:
                print(f"notify [{notif['severity']}]: {notif['message']}")
        else:
            print(f"\nFAILED: {res.error}")
        return 0 if res.ok else 1

    if cmd == "author":
        if len(argv) < 2:
            print('author needs a request, e.g. author "draft a bank freeze letter..."')
            return 1
        mem.close()
        from .agent_loop import AgentLoop
        loop = AgentLoop(cfg)
        loop.load_workflows()
        result = loop.author_workflow(argv[1], register="--register" in argv[2:])
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if cmd == "run":
        mem.close()  # AgentLoop opens its own connection
        from .agent_loop import AgentLoop
        from .ipc import serve
        loop = AgentLoop(cfg)
        loop.start()
        print(f"[agent] serving on http://{cfg.ipc.host}:{cfg.ipc.port}")
        try:
            serve(loop, cfg.ipc.host, cfg.ipc.port)
        except KeyboardInterrupt:
            pass
        finally:
            loop.stop()
        return 0

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
