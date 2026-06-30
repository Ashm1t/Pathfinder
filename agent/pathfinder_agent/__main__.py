"""CLI entrypoint for the foundation slice.

Usage:
    python -m pathfinder_agent scan <folder>     # structural ingest + show panels
    python -m pathfinder_agent panels            # show panels from existing memory
"""
from __future__ import annotations

import json
import sys

from .analyzer import scan_and_ingest
from .config import Config
from .memory import AgentMemory
from .panels import all_panels


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
        n = scan_and_ingest(mem, folder)
        print(f"Scanned {folder}: {n} case folder(s) ingested (structural pass).\n")
        print(json.dumps(all_panels(mem), indent=2, ensure_ascii=False))
        return 0

    if cmd == "panels":
        print(json.dumps(all_panels(mem), indent=2, ensure_ascii=False))
        return 0

    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
