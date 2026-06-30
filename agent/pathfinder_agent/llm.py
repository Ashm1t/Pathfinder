"""LLM adapter — Ollama over its REST API, behind a swappable interface.

Uses stdlib urllib so the core has no hard dependency on the `ollama` package.
The factory `make_llm_adapter` is the hot-swap seam: add vLLM / OpenAI-compat
here and only the config changes.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List

from .config import LlmConfig


@dataclass
class LlmResponse:
    ok: bool = False
    content: str = ""
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LlmAdapter(ABC):
    @abstractmethod
    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.1, max_tokens: int = 1024) -> LlmResponse: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


class OllamaAdapter(LlmAdapter):
    def __init__(self, cfg: LlmConfig):
        self._cfg = cfg

    @property
    def name(self) -> str:
        return "ollama"

    def _request(self, method: str, path: str, body: dict, timeout: float) -> dict:
        url = self._cfg.base_url.rstrip("/") + path
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def chat(self, messages, temperature=0.1, max_tokens=1024) -> LlmResponse:
        body = {
            "model": self._cfg.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self._cfg.context_window,
            },
        }
        try:
            r = self._request("POST", "/api/chat", body, self._cfg.timeout_s)
            return LlmResponse(
                ok=True,
                content=r.get("message", {}).get("content", ""),
                prompt_tokens=r.get("prompt_eval_count", 0),
                completion_tokens=r.get("eval_count", 0),
            )
        except (urllib.error.URLError, OSError, ValueError) as e:
            return LlmResponse(ok=False, error=str(e))

    def is_available(self) -> bool:
        try:
            self._request("GET", "/api/tags", {}, 3.0)
            return True
        except Exception:
            return False


def make_llm_adapter(cfg: LlmConfig) -> LlmAdapter:
    if cfg.adapter in ("ollama", ""):
        return OllamaAdapter(cfg)
    # Hot-swap point: add "vllm" / "openai_compat" here.
    raise ValueError(f"Unknown LLM adapter: {cfg.adapter}")
