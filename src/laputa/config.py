"""Load Laputa config. Path resolved by paths.resolve_paths() (dev .laputa/, LAPUTA_HOME, or XDG)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    backend: str = "openai"          # openai | anthropic | ollama | <litellm prefix>
    model: str = "gpt-4o-mini"
    api_base: str | None = None      # set for ollama or openai-compatible endpoints
    api_key_env: str | None = None   # env var holding the api key (else litellm default)
    api_key: str | None = None       # inline key (gitignored config only; env is safer)
    temperature: float = 0.0


class CompileConfig(BaseModel):
    chunk_lines: int = 60


class NsConfig(BaseModel):
    default: list[str] = Field(default_factory=lambda: ["public"])
    token_map: dict[str, list[str]] = Field(default_factory=dict)


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    ns: NsConfig = Field(default_factory=NsConfig)
    install_source: str | None = None      # pypi | local | git+URL | path


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
