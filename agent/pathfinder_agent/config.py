"""Configuration — loaded from config/pathfinder.json, with sane defaults.

Mirrors the C++ Config so the design carries over. The LLM section is the
hot-swap seam: change `adapter`/`base_url`/`model` to move Ollama -> vLLM etc.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import List


@dataclass
class LlmConfig:
    adapter: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    temperature: float = 0.1
    max_tokens: int = 2048
    context_window: int = 4096
    timeout_s: int = 60


@dataclass
class AgentConfig:
    watched_folders: List[str] = field(default_factory=list)
    poll_interval_s: float = 5.0
    max_file_size_mb: int = 50
    supported_extensions: List[str] = field(
        default_factory=lambda: [".txt", ".md", ".docx", ".pdf"]
    )


@dataclass
class MemoryConfig:
    db_path: str = "pathfinder_memory.db"
    fact_ttl_days: int = 90


@dataclass
class IpcConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ipc: IpcConfig = field(default_factory=IpcConfig)

    @staticmethod
    def load(path: str) -> "Config":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return Config()

        return Config(
            llm=LlmConfig(**{**asdict(LlmConfig()), **data.get("llm", {})}),
            agent=AgentConfig(**{**asdict(AgentConfig()), **data.get("agent", {})}),
            memory=MemoryConfig(**{**asdict(MemoryConfig()), **data.get("memory", {})}),
            ipc=IpcConfig(**{**asdict(IpcConfig()), **data.get("ipc", {})}),
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
