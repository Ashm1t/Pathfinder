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

    @app.get("/cases/{case_id}/facts")
    def case_facts(case_id: str):
        """Every stored fact with confidence + source, for audit display."""
        facts = loop.memory.get_all_facts(case_id)
        return {"case_id": case_id, "facts": [{
            "type": f.type.value, "key": f.key, "value": f.value,
            "confidence": f.confidence, "source_file": f.source_file,
            "source_page": f.source_page, "extracted_at": f.extracted_at,
            "event_date_ms": f.event_date_ms,
        } for f in facts]}

    @app.get("/cases/{case_id}/history")
    def case_history(case_id: str):
        """Audit trail: archived old values of facts that changed."""
        return {"case_id": case_id,
                "history": loop.memory.get_fact_history(case_id)}

    @app.get("/workflows")
    def workflows():
        """Full definitions, so the HUD can render real steps, not mock data."""
        return {"workflows": loop.list_workflows()}

    @app.post("/workflow/register")
    def register_workflow(payload: dict):
        """Register a previously-authored (and officer-reviewed) definition.

        Body: {"workflow": {...}} — validated against the blueprint again
        before it is accepted and persisted.
        """
        wf = payload.get("workflow")
        if not isinstance(wf, dict):
            return {"ok": False, "errors": ["'workflow' object is required"]}
        errors = loop.register_workflow(wf)
        return {"ok": not errors, "errors": errors}

    @app.post("/workflow/author")
    def author_workflow(payload: dict):
        """Create a workflow from a natural-language request.

        Body: {"request": "<what the officer wants>", "register": bool}
        A valid result is only added to the engine when register is true —
        the intended flow is: author, show the officer, then register.
        """
        request = str(payload.get("request", "")).strip()
        if not request:
            return {"ok": False, "workflow": None,
                    "errors": ["'request' is required"], "registered": False}
        return loop.author_workflow(request,
                                    register=bool(payload.get("register", False)))

    return app


def serve(loop: "AgentLoop", host: str, port: int) -> None:
    import uvicorn
    app = create_app(loop)
    uvicorn.run(app, host=host, port=port, log_level="info")
