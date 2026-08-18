"""Single source of truth for the coding agents lorekeep integrates with.

Every agent-specific fact — how to detect it, where to write its MCP config
and lifecycle hook, which importer reads its transcripts — lives in one
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
from typing import Literal


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
    hook_resolve: str = "session_from_hook"
    deep_fn: str | None = None


HookTrigger = Literal["session_end", "idle_fallback", "turn_end_fallback"]


@dataclass(frozen=True, slots=True)
class HookSpec:
    """One agent's real lifecycle-hook contract.

    ``trigger`` describes semantics, not spelling: only ``session_end`` is an
    exact end boundary.  Fallback events are coalesced by the daemon before
    transcript ingest.
    """

    event: str
    trigger: HookTrigger
    project_path: str | None
    user_path: str | None
    timeout_seconds: int
    surfaces: tuple[str, ...] = ("local",)

    @property
    def exact(self) -> bool:
        return self.trigger == "session_end"


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
    hook: HookSpec | None = None
    project_config: str = ""
    user_config: str = ""

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
        if self.hook is None:
            return None
        if scope == "project" and self.hook.project_path is None:
            return None
        return self.writer().hook_target(target_dir, scope)

    @property
    def supports_hook(self) -> bool:
        """Derived compatibility view; capability truth lives in ``hook``."""
        return self.hook is not None

    @property
    def project_hook(self) -> str | None:
        return self.hook.project_path if self.hook else None

    @property
    def user_hook(self) -> str | None:
        return self.hook.user_path if self.hook else None


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="claude",
        label="Claude Code",
        active_env=("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"),
        home_env="CLAUDE_CONFIG_DIR",
        install_markers=("~/.claude",),
        binaries=("claude",),
        data_markers=("~/.claude/projects",),
        writer_module="lorekeep.integrations.claude_code",
        hook=HookSpec(
            event="SessionEnd", trigger="session_end",
            project_path=".claude/settings.json",
            user_path="~/.claude/settings.json", timeout_seconds=30,
        ),
        project_config=".mcp.json",
        user_config="~/.claude.json",
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
        hook=HookSpec(
            event="SessionEnd", trigger="session_end",
            project_path=".codex/hooks.json",
            user_path="~/.codex/hooks.json", timeout_seconds=3,
        ),
        project_config="config.toml",
        user_config="~/.codex/config.toml",
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
        hook=HookSpec(
            event="sessionEnd", trigger="session_end",
            # Cursor cloud loads project hook files, but does not fire the
            # IDE-lifetime sessionEnd event. Local project capture is safe.
            project_path=".cursor/hooks.json",
            user_path="~/.cursor/hooks.json", timeout_seconds=30,
            surfaces=("local-ide",),
        ),
        project_config=".cursor/mcp.json",
        user_config="~/.cursor/mcp.json",
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
        hook=HookSpec(
            event="session.idle", trigger="idle_fallback",
            project_path=".opencode/plugins/lorekeep.ts",
            user_path="~/.config/opencode/plugins/lorekeep.ts",
            timeout_seconds=30,
        ),
        project_config="opencode.json",
        user_config="~/.config/opencode/opencode.json",
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
        hook=HookSpec(
            event="SessionEnd", trigger="session_end",
            project_path=".grok/hooks/lorekeep.json",
            user_path="~/.grok/hooks/lorekeep.json", timeout_seconds=30,
        ),
        user_config="~/.grok/config.toml",
        project_config=".grok/config.toml",
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
        home_env="QODER_CONFIG_DIR",
        install_markers=("~/.qoder",),
        binaries=("qoder",),
        data_markers=("~/.qoder/projects",),
        writer_module="lorekeep.integrations.qoder",
        hook=HookSpec(
            event="SessionEnd", trigger="session_end",
            project_path=".qoder/settings.json",
            user_path="~/.qoder/settings.json", timeout_seconds=30,
        ),
        project_config=".mcp.json",
        user_config="~/.qoder/settings.json",
        importer_module="lorekeep.importer.qoder",
        session_ns="qoder-session",
        session=SessionSource(
            locate="locate_session", parse="parse_transcript",
            key="session_key", handle_kind="file",
        ),
    ),
    AgentSpec(
        name="copilot",
        label="GitHub Copilot",
        home_env="COPILOT_HOME",
        install_markers=("~/.copilot",),
        binaries=("copilot",),
        data_markers=("~/.copilot/session-state",),
        writer_module="lorekeep.integrations.copilot",
        hook=HookSpec(
            event="sessionEnd", trigger="session_end",
            project_path=None,
            user_path="~/.copilot/hooks/lorekeep.json", timeout_seconds=5,
            surfaces=("local-cli",),
        ),
        project_config=".github/mcp.json",
        user_config="~/.copilot/mcp-config.json",
        importer_module="lorekeep.importer.copilot",
        session_ns="copilot-session",
        session=SessionSource(
            locate="locate_session", parse="parse_transcript",
            key="session_key", handle_kind="file",
        ),
    ),
    AgentSpec(
        name="cmd",
        label="Command Code",
        install_markers=("~/.commandcode",),
        binaries=("cmd", "command-code"),
        data_markers=("~/.commandcode/projects",),
        writer_module="lorekeep.integrations.commandcode",
        hook=HookSpec(
            event="Stop", trigger="turn_end_fallback",
            project_path=".commandcode/settings.json",
            user_path="~/.commandcode/settings.json", timeout_seconds=30,
        ),
        project_config=".commandcode/mcp.json",
        user_config="~/.commandcode/mcp.json",
        importer_module="lorekeep.importer.commandcode",
        session_ns="cmd-session",
        session=SessionSource(
            locate="locate_session", parse="parse_transcript",
            key="session_key", handle_kind="file",
        ),
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
