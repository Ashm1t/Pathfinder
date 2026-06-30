"""IPC — localhost HTTP boundary the C++ HUD reads from.

Thin, read-mostly REST surface over the AgentLoop. FastAPI is optional at
import time so the rest of the package works without it installed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_loop import AgentLoop


def create_app(loop: "AgentLoop"):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="Pathfinder Agent", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
        allow_headers=["*"])

    @app.get("/health")
    def health():
        return loop.status()

    @app.get("/panels")
    def panels():
        return loop.get_panels()

    @app.get("/panels/chronology/{case_id}")
    def chronology(case_id: str):
        return {"case_id": case_id, "entries": loop.get_chronology(case_id)}

    @app.get("/notifications")
    def notifications():
        return {"notifications": loop.notifications()}

    @app.post("/workflow/{workflow_id}/run")
    def run_workflow(workflow_id: str, case_id: str = ""):
        res = loop.run_workflow(workflow_id, case_id)
        return {"ok": res.ok, "error": res.error,
                "log": res.log, "context": res.context}

    return app


def serve(loop: "AgentLoop", host: str, port: int) -> None:
    import uvicorn
    app = create_app(loop)
    uvicorn.run(app, host=host, port=port, log_level="info")
