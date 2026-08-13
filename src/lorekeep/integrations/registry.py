"""Single source of truth for the coding agents lorekeep integrates with.

Every agent-specific fact — how to detect it, where to write its MCP config
and session-end hook, which importer reads its transcripts — lives in one
:class:`AgentSpec` here.  Before this module those facts were scattered across
six if/elif ladders and three parallel dicts, so adding or fixing an agent
meant finding all of them.

The specs reference their writer and importer by **dotted module string**, not
by import.  Two reasons: importing this module must not drag in ``sqlite3``
(cursor / opencode importers) on every CLI startup, and ``detect.py`` needs the
detection fields without an import cycle back through the writers.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True, slots=True)
class MemorySource:
    """Agent-authored memory files — small, curated, zero LLM cost to ingest.

    Both attributes name functions on the agent's importer module:
    ``dir_finder() -> Path | None`` and
    ``import_fn(raw_root, *, namespace, dry_run=False) -> list[Path]``.
    """

    dir_finder: str
    import_fn: str


@dataclass(frozen=True, slots=True)
class SessionSource:
    """Machine-dumped conversation transcripts — large, capped, pruned.

    Attributes name functions on the agent's importer module:
    ``locate(cwd) -> handle | None``, ``parse(handle) -> list[ConversationTurn]``,
    ``key(handle) -> str`` (a stable session identifier),
    ``dump_fn(raw_root, cwd, *, namespace, dry_run, **limits) -> list[Path]``
    (the zero-LLM path), and the optional ``deep_fn`` LLM-summarizing path.
    """

    locate: str
    parse: str
    key: str
    handle_kind: str
    dump_fn: str = "dump_current_session"
    deep_fn: str | None = None


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Everything lorekeep knows about one coding agent."""

    name: str
    label: str

    # ── detection ────────────────────────────────────────────────────────
    # Env vars the agent sets in shells it spawns.
    active_env: tuple[str, ...] = ()
    # Env var that relocates the agent's own home, if it has one.
    home_env: str | None = None
    install_markers: tuple[str, ...] = ()
    binaries: tuple[str, ...] = ()
    # Where the importer actually reads. An install marker can exist while
    # this does not, which is why `agent detect` reports the two separately.
    data_markers: tuple[str, ...] = ()

    # ── wiring ───────────────────────────────────────────────────────────
    writer_module: str = ""
    supports_hook: bool = False
    project_config: str = ""
    user_config: str = ""
    project_hook: str | None = None
    user_hook: str | None = None

    # ── ingest ───────────────────────────────────────────────────────────
    importer_module: str = ""
    memory_ns: str | None = None
    session_ns: str | None = None
    memory: MemorySource | None = None
    session: SessionSource | None = None

    def writer(self) -> ModuleType:
        return importlib.import_module(self.writer_module)

    def importer(self) -> ModuleType | None:
        if not self.importer_module:
            return None
        return importlib.import_module(self.importer_module)

    def config_path(self, target_dir: Path, scope: str = "project") -> Path:
        """Resolve the real MCP config target.

        The declared ``project_config`` / ``user_config`` strings are data that
        tests cross-check; resolution goes through the writer because only it
        applies the agent's own env overrides (``CODEX_HOME``,
        ``XDG_CONFIG_HOME``).
        """
        return self.writer().config_target(target_dir, scope)

    def hook_path(self, target_dir: Path, scope: str = "project") -> Path | None:
        if not self.supports_hook:
            return None
        return self.writer().hook_target(target_dir, scope)


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="claude",
        label="Claude Code",
        active_env=("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"),
        install_markers=("~/.claude",),
        binaries=("claude",),
        data_markers=("~/.claude/projects",),
        writer_module="lorekeep.integrations.claude_code",
        supports_hook=True,
        project_config=".mcp.json",
        user_config="~/.claude.json",
        project_hook=".claude/settings.json",
        user_hook="~/.claude/settings.json",
        importer_module="lorekeep.importer.claude",
        memory_ns="claude-memory",
        session_ns="claude-session",
        memory=MemorySource(dir_finder="memories_dir", import_fn="quick_import"),
        session=SessionSource(
            locate="locate_session", parse="parse_transcript",
            key="session_key", handle_kind="file",
            deep_fn="import_session_deep",
        ),
    ),
    AgentSpec(
        name="codex",
        label="Codex CLI",
        active_env=("CODEX_SANDBOX",),
        home_env="CODEX_HOME",
        install_markers=("~/.codex",),
        binaries=("codex",),
        data_markers=("~/.codex/sessions", "~/.codex/memories"),
        writer_module="lorekeep.integrations.codex",
        supports_hook=True,
        project_config="config.toml",
        user_config="~/.codex/config.toml",
        project_hook=".codex/hooks.json",
        user_hook="~/.codex/hooks.json",
        importer_module="lorekeep.importer.codex",
        memory_ns="codex-memory",
        session_ns="codex-session",
        memory=MemorySource(dir_finder="memories_dir", import_fn="quick_import"),
        session=SessionSource(
            locate="locate_session", parse="parse_rollout",
            key="session_key", handle_kind="file",
            deep_fn="import_session_deep",
        ),
    ),
    AgentSpec(
        name="cursor",
        label="Cursor",
        active_env=("CURSOR_DEBUG",),
        install_markers=("~/.cursor",),
        binaries=("cursor",),
        data_markers=(
            "~/.config/Cursor/User/globalStorage/state.vscdb",
            "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
        ),
        writer_module="lorekeep.integrations.cursor",
        supports_hook=True,
        project_config=".cursor/mcp.json",
        user_config="~/.cursor/mcp.json",
        project_hook=".cursor/hooks.json",
        user_hook="~/.cursor/hooks.json",
        importer_module="lorekeep.importer.cursor",
        session_ns="cursor-session",
        session=SessionSource(
            locate="locate_session", parse="parse_composer_turns",
            key="session_key", handle_kind="blob",
        ),
    ),
    AgentSpec(
        name="opencode",
        label="opencode",
        active_env=("OPENCODE",),
        install_markers=("~/.config/opencode", "~/.opencode"),
        binaries=("opencode",),
        data_markers=("~/.local/share/opencode/opencode.db",),
        writer_module="lorekeep.integrations.opencode",
        supports_hook=True,
        project_config="opencode.json",
        user_config="~/.config/opencode/opencode.json",
        project_hook=".opencode/plugins/lorekeep.ts",
        user_hook="~/.config/opencode/plugins/lorekeep.ts",
        importer_module="lorekeep.importer.opencode",
        session_ns="opencode-session",
        session=SessionSource(
            locate="locate_session", parse="parse_session",
            key="session_key", handle_kind="id",
            deep_fn="import_session_deep",
        ),
    ),
    AgentSpec(
        name="grok",
        label="Grok Build",
        install_markers=("~/.grok",),
        binaries=("grok",),
        data_markers=("~/.grok/sessions",),
        writer_module="lorekeep.integrations.grok",
        supports_hook=False,
        user_config="~/.grok/config.toml",
        project_config="",  # Grok Build is user-scope only.
        importer_module="lorekeep.importer.grok",
        session_ns="grok-session",
        session=SessionSource(
            locate="locate_session", parse="parse_transcript",
            key="session_key", handle_kind="dir",
            dump_fn="dump_current_session",
            deep_fn="import_session_deep",
        ),
    ),
    AgentSpec(
        name="qoder",
        label="Qoder",
        install_markers=("~/.qoder",),
        binaries=(),
        data_markers=("~/.qoder/projects",),
        writer_module="lorekeep.integrations.qoder",
        supports_hook=False,
        project_config=".qoder/mcp.json",
        user_config="~/.qoder/mcp.json",
        # Importer not yet implemented — Qoder stores sessions as JSON
        # (state.json per project) that need an LLM summarization path.
        importer_module="",
    ),
    AgentSpec(
        name="copilot",
        label="GitHub Copilot",
        install_markers=("~/.copilot",),
        binaries=("copilot",),
        data_markers=("~/.copilot",),
        writer_module="lorekeep.integrations.copilot",
        supports_hook=False,
        project_config=".github/mcp.json",
        user_config="~/.copilot/mcp-config.json",
        # Importer not yet implemented.
        importer_module="",
    ),
    AgentSpec(
        name="cmd",
        label="Command Code",
        install_markers=("~/.commandcode",),
        binaries=("cmd", "command-code"),
        data_markers=("~/.commandcode",),
        writer_module="lorekeep.integrations.commandcode",
        supports_hook=False,
        project_config=".commandcode/mcp.json",
        user_config="~/.commandcode/mcp.json",
        # Importer not yet implemented.
        importer_module="",
    ),
)

AGENT_NAMES: tuple[str, ...] = tuple(s.name for s in _SPECS)


def all_specs() -> tuple[AgentSpec, ...]:
    return _SPECS


def find(name: str) -> AgentSpec | None:
    for spec in _SPECS:
        if spec.name == name:
            return spec
    return None


def get(name: str) -> AgentSpec:
    spec = find(name)
    if spec is None:
        raise KeyError(name)
    return spec
