"""Minimal synchronous MCP (JSON-RPC 2.0 over stdio) client.

Spawns a standard MCP server (e.g. @modelcontextprotocol/server-filesystem),
performs the handshake, and exposes call_tool/list_tools. Synchronous: a
background reader thread routes responses to waiting callers by id.

This covers the *action* side (Office/Playwright MCPs for doc generation and
.gov automation). Plain local reads/enumeration are done directly in Python.
The official async `mcp` SDK can replace this later if richer features are needed.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Dict, List, Optional


class McpClient:
    def __init__(self, command: str, args: List[str]):
        self._command = command
        self._args = args
        self._proc: Optional[subprocess.Popen] = None
        self._next_id = 1
        self._pending: Dict[int, dict] = {}
        self._events: Dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._connected = False

    def connect(self, timeout: float = 30.0) -> bool:
        # On Windows, npx/npm are .cmd shims — launch via the shell.
        if os.name == "nt":
            cmdline = subprocess.list2cmdline([self._command] + self._args)
            self._proc = subprocess.Popen(
                cmdline, shell=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        else:
            self._proc = subprocess.Popen(
                [self._command] + self._args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)

        self._connected = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        resp = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Pathfinder", "version": "0.1.0"},
        }, timeout)
        if "error" in resp:
            self.close()
            return False
        self._notify("notifications/initialized", {})
        return True

    def close(self) -> None:
        self._connected = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and msg["id"] is not None:
                rid = msg["id"]
                with self._lock:
                    self._pending[rid] = msg
                    ev = self._events.get(rid)
                if ev:
                    ev.set()

    def _write(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            ev = threading.Event()
            self._events[rid] = ev
        self._write({"jsonrpc": "2.0", "id": rid, "method": method,
                     "params": params})
        if not ev.wait(timeout):
            return {"error": "timeout"}
        with self._lock:
            resp = self._pending.pop(rid, {})
            self._events.pop(rid, None)
        return resp

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def call_tool(self, name: str, arguments: dict) -> dict:
        resp = self._request("tools/call", {"name": name, "arguments": arguments})
        if "error" in resp:
            return {"ok": False, "error": resp["error"]}
        return {"ok": True, "result": resp.get("result", {})}

    def list_tools(self) -> List[dict]:
        resp = self._request("tools/list", {})
        return resp.get("result", {}).get("tools", [])
