"""Load local Laputa config. Lives in .laputa/config.yaml (gitignored)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    backend: str = "openai"          # openai | anthropic | ollama | <litellm prefix>
    model: str = "gpt-4o-mini"
    api_base: str | None = None      # set for ollama, e.g. http://localhost:11434
    temperature: float = 0.0


class CompileConfig(BaseModel):
    chunk_lines: int = 60


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
