"""Load Lorekeep config. Path resolved by paths.resolve_paths() (dev .lorekeep/, LOREKEEP_HOME, or ~/.lorekeep)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    model: str = "openai/gpt-4o-mini"   # must be {provider}/{model} — litellm routes by prefix
    api_base: str | None = None      # set for ollama or openai-compatible endpoints
    api_key_env: str | None = None   # env var holding the api key (else litellm default)
    api_key: str | None = None       # inline key (gitignored config only; env is safer)
    temperature: float = 0.0
    timeout_seconds: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)


class CompileConfig(BaseModel):
    chunk_lines: int = 60
    # ISO 639-1 alpha-2 code, normalized to lowercase by contract.
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")


class NsConfig(BaseModel):
    default: list[str] = Field(default_factory=lambda: ["public"])
    personal: str | None = None
    token_map: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def personal_namespace(self) -> str:
        """Personal/subject namespace, with a safe legacy-config fallback."""
        if self.personal:
            return self.personal
        return next((ns for ns in self.default if ns != "public"), "me")


class ObservabilityConfig(BaseModel):
    """Optional observability integration via litellm callbacks."""
    provider: str | None = None      # langfuse | langsmith
    api_key_env: str | None = None   # env var name (e.g. LANGFUSE_PUBLIC_KEY)
    project: str | None = None       # project name / dataset name
    api_url: str | None = None       # self-hosted endpoint (langfuse)


class BugReportConfig(BaseModel):
    """Automatic GitHub issue creation for runtime errors."""
    enabled: bool = True
    repo: str = "manhhailua/lorekeep"
    token_env: str = "LOREKEEP_GITHUB_TOKEN"  # env var name holding the GitHub PAT
    labels: list[str] = Field(default_factory=lambda: ["auto-reported"])


class AgentsConfig(BaseModel):
    """Detection, wiring, and session ingest for coding agents."""
    auto_wire: bool = True                   # daemon re-wires detected agents each cycle
    wire_scope: str = "user"                 # user | project
    wire_interval_seconds: int = Field(default=900, gt=0)
    enabled: list[str] = Field(
        default_factory=lambda: [
            "claude", "codex", "cursor", "opencode", "grok", "qoder",
        ]
    )
    watch_transcripts: bool = True           # zero-LLM dump → raw/<agent>-session/
    transcript_max_batches: int = Field(default=20, gt=0)
    transcript_max_chars: int = Field(default=20_000, gt=0)
    transcript_retain_sessions: int = Field(default=5, gt=0)
    deep_import: bool = False                # advanced opt-in: LLM summarization
    self_heal: bool = True                   # daemon auto-heals graph after compile
    auto_backup: bool = True                 # daemon auto-backups after graph changes


class BackupConfig(BaseModel):
    """Backup repository configuration."""
    branch: str = "main"                     # git branch for the backup repo
    auto_resolve_durable: bool = False       # LLM-assisted merge for durable conflicts


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    ns: NsConfig = Field(default_factory=NsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    bugreport: BugReportConfig = Field(default_factory=BugReportConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    install_source: str | None = None      # pypi | local | git+URL | path


def _validate_provider(cfg: Config) -> None:
    """Fail fast on a bare model name.

    A bare name (no ``/``) fails deep inside litellm with the opaque
    ``LLM Provider NOT provided`` error. ``validate_model_prefix`` raises
    ``ValueError`` with an actionable suggestion instead.
    """
    from lorekeep.providers import validate_model_prefix

    if cfg.provider.model:
        validate_model_prefix(cfg.provider.model)


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(data)
    _validate_provider(cfg)
    return cfg
