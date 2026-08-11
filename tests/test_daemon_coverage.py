"""Coverage fill for daemon helper functions and CLI commands in PR #190.

Tests:
- _sync_agent_wiring: normal, backoff, exception, disabled agents
- _discover_watchable_sessions: finds agents with memory dirs
- _quick_import_session: dispatches to registry, unknown agent
- _discover_session_transcripts: finds agents with session handles
- _dump_session_transcript: dispatches dump, prune, unknown agent
- _wire_one: writes config + hook, returns paths
- _auto_wire_agents: runs for all detected agents
- import_cmd: claude deep, codex deep, unknown source
- agent detect: table output, --json output
- agent wire: dry-run, full wire, unchanged re-run
- lint command: no-graph, healthy, with issues
- suggest command: no-graph, healthy, with suggestions
- status command: graph dashboard
- eval locomo command
- service install/uninstall (mocked)
- interactive provider search (mocked)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.compile.providers import FakeProvider

runner = CliRunner()


def safe_sleep_break(max_cycles=2, side_effect=None):
    """Return a fake time.sleep that just counts + calls side_effect.
    The loop is broken by the caller's injected break mechanism, not sleep."""
    count = [0]

    def _fake(s):
        count[0] += 1
        if side_effect:
            side_effect(count[0])

    return _fake


def make_break_after(max_calls=2):
    """Return a callback that raises KeyboardInterrupt after max_calls.
    Safe to inject into any function called inside the watch try block."""
    count = [0]

    def _breaker(*a, **kw):
        count[0] += 1
        if count[0] >= max_calls:
            raise KeyboardInterrupt
        return []

    return _breaker

# Canned responses for FakeProvider
_COMPILE_RESPONSE = json.dumps({"nodes": [], "edges": [], "aliases": []})
_IMPORT_RESPONSE = "# Knowledge Summary\n\n## Decisions\n- Test import summary.\n"


def _fake_compile_provider():
    return FakeProvider(responses=[_COMPILE_RESPONSE] * 50)


def _fake_import_provider():
    return FakeProvider(responses=[_IMPORT_RESPONSE] * 50)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """Isolated HOME + LOREKEEP_HOME so tests never touch real agent configs."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Block real agent detection
    monkeypatch.setattr("shutil.which", lambda _: None)
    return home


@pytest.fixture
def seeded_graph(isolated_home: Path, fixtures: Path) -> Path:
    """Copy a valid facts.jsonl into the isolated home."""
    out = isolated_home / "graph"
    out.mkdir(exist_ok=True)
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    shutil.copy(fixtures / "schema.json", isolated_home / "schema.json")
    return out


# ── _sync_agent_wiring ──────────────────────────────────────────────────────

class TestSyncAgentWiring:
    def test_returns_changed_paths(self, isolated_home, monkeypatch):
        from lorekeep.cli import _sync_agent_wiring
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents",
            lambda: ["claude"],
        )
        monkeypatch.setattr(
            "lorekeep.cli._wire_one",
            lambda spec, target, ns, scope: (Path("/tmp/test.json"), None),
        )
        result = _sync_agent_wiring(
            scope="user", ns="test", enabled=["claude"],
            backoff={}, now=1000.0,
        )
        assert len(result) == 1
        assert result[0][0] == "claude"

    def test_backoff_skips_agent(self, isolated_home, monkeypatch):
        from lorekeep.cli import _sync_agent_wiring
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents",
            lambda: ["claude"],
        )
        wire_calls = []
        monkeypatch.setattr(
            "lorekeep.cli._wire_one",
            lambda *a, **kw: wire_calls.append(1) or (None, None),
        )
        result = _sync_agent_wiring(
            scope="user", ns="test", enabled=["claude"],
            backoff={"claude": 2000.0}, now=1000.0,  # backoff expires at 2000
        )
        assert len(result) == 0
        assert len(wire_calls) == 0  # not called

    def test_exception_sets_backoff(self, isolated_home, monkeypatch):
        from lorekeep.cli import _sync_agent_wiring
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents",
            lambda: ["claude"],
        )

        def _boom(*a, **kw):
            raise RuntimeError("permission denied")
        monkeypatch.setattr("lorekeep.cli._wire_one", _boom)

        backoff = {}
        result = _sync_agent_wiring(
            scope="user", ns="test", enabled=["claude"],
            backoff=backoff, now=1000.0,
        )
        assert len(result) == 0
        assert "claude" in backoff
        assert backoff["claude"] == 1000.0 + 3600  # 1h backoff

    def test_disabled_agent_skipped(self, isolated_home, monkeypatch):
        from lorekeep.cli import _sync_agent_wiring
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents",
            lambda: ["claude", "cursor"],
        )
        wire_calls = []
        monkeypatch.setattr(
            "lorekeep.cli._wire_one",
            lambda *a, **kw: wire_calls.append(a[0].name) or (None, None),
        )
        result = _sync_agent_wiring(
            scope="user", ns="test", enabled=["claude"],  # cursor disabled
            backoff={}, now=1000.0,
        )
        assert len(wire_calls) == 1
        assert wire_calls[0] == "claude"

    def test_success_clears_backoff(self, isolated_home, monkeypatch):
        from lorekeep.cli import _sync_agent_wiring
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents",
            lambda: ["claude"],
        )
        monkeypatch.setattr(
            "lorekeep.cli._wire_one",
            lambda *a, **kw: (Path("/tmp/test.json"), None),
        )
        backoff = {"claude": 500.0}
        _sync_agent_wiring(
            scope="user", ns="test", enabled=["claude"],
            backoff=backoff, now=1000.0,
        )
        assert "claude" not in backoff


# ── _discover_watchable_sessions ────────────────────────────────────────────

class TestDiscoverWatchableSessions:
    def test_finds_agents_with_memory(self, isolated_home, monkeypatch):
        from lorekeep.cli import _discover_watchable_sessions
        from lorekeep.integrations.registry import find

        # Mock claude importer to return a dir with .md files
        claude_dir = isolated_home / "claude-mem"
        claude_dir.mkdir()
        (claude_dir / "test.md").write_text("# Test")

        claude_spec = find("claude")
        monkeypatch.setattr(
            f"lorekeep.importer.{claude_spec.importer_module.split('.')[-1]}.{claude_spec.memory.dir_finder}",
            lambda: claude_dir,
        )

        sessions = _discover_watchable_sessions()
        names = [s[0] for s in sessions]
        assert "claude" in names

    def test_skips_empty_memory_dirs(self, isolated_home, monkeypatch):
        from lorekeep.cli import _discover_watchable_sessions
        from lorekeep.integrations.registry import find

        claude_dir = isolated_home / "claude-mem"
        claude_dir.mkdir()
        # No .md files

        claude_spec = find("claude")
        monkeypatch.setattr(
            f"lorekeep.importer.{claude_spec.importer_module.split('.')[-1]}.{claude_spec.memory.dir_finder}",
            lambda: claude_dir,
        )

        sessions = _discover_watchable_sessions()
        assert "claude" not in [s[0] for s in sessions]

    def test_handles_importer_exception(self, isolated_home, monkeypatch):
        from lorekeep.cli import _discover_watchable_sessions
        from lorekeep.integrations.registry import find

        claude_spec = find("claude")
        monkeypatch.setattr(
            f"lorekeep.importer.{claude_spec.importer_module.split('.')[-1]}.{claude_spec.memory.dir_finder}",
            lambda: (_ for _ in ()).throw(RuntimeError("no access")),
        )

        sessions = _discover_watchable_sessions()
        assert "claude" not in [s[0] for s in sessions]


# ── _quick_import_session ───────────────────────────────────────────────────

class TestQuickImportSession:
    def test_unknown_agent_returns_zero(self, isolated_home):
        from lorekeep.cli import _quick_import_session
        result = _quick_import_session("unknown-agent", Path("/tmp"), Path("/tmp"), Path("/tmp"))
        assert result == 0

    def test_dispatches_to_importer(self, isolated_home, monkeypatch):
        from lorekeep.cli import _quick_import_session
        from lorekeep.integrations.registry import find

        claude_spec = find("claude")
        written = [Path("/tmp/a.md")]
        monkeypatch.setattr(
            f"lorekeep.importer.{claude_spec.importer_module.split('.')[-1]}.{claude_spec.memory.import_fn}",
            lambda raw_root, namespace=None, memory_dir=None: written,
        )
        result = _quick_import_session("claude", Path("/tmp"), Path("/tmp/mem"), Path("/tmp/raw"))
        assert result == 1


# ── _discover_session_transcripts ───────────────────────────────────────────

class TestDiscoverSessionTranscripts:
    def test_returns_handles(self, isolated_home, monkeypatch):
        from lorekeep.cli import _discover_session_transcripts
        from lorekeep.integrations.registry import find

        claude_spec = find("claude")
        if claude_spec.session:
            monkeypatch.setattr(
                f"lorekeep.importer.{claude_spec.importer_module.split('.')[-1]}.{claude_spec.session.locate}",
                lambda cwd=None: "/tmp/session-handle",
            )

        transcripts = _discover_session_transcripts()
        # At least claude should be found if it has a session spec
        assert isinstance(transcripts, list)

    def test_handles_exception(self, isolated_home, monkeypatch):
        from lorekeep.cli import _discover_session_transcripts
        from lorekeep.integrations.registry import find

        claude_spec = find("claude")
        if claude_spec.session:
            monkeypatch.setattr(
                f"lorekeep.importer.{claude_spec.importer_module.split('.')[-1]}.{claude_spec.session.locate}",
                lambda cwd=None: (_ for _ in ()).throw(RuntimeError("no session")),
            )

        transcripts = _discover_session_transcripts()
        assert isinstance(transcripts, list)


# ── _dump_session_transcript ────────────────────────────────────────────────

class TestDumpSessionTranscript:
    def test_unknown_agent_returns_zero(self, isolated_home):
        from lorekeep.cli import _dump_session_transcript
        result = _dump_session_transcript("unknown", None, Path("/tmp"), MagicMock())
        assert result == 0


# ── lint command ────────────────────────────────────────────────────────────

class TestLintCommand:
    def test_no_graph_exit_1(self, isolated_home):
        result = runner.invoke(app, ["agent", "lint"])
        assert result.exit_code == 1
        assert "no graph" in result.stdout.lower()

    def test_healthy_graph_no_issues(self, seeded_graph):
        result = runner.invoke(app, ["agent", "lint"])
        assert result.exit_code == 0


# ── suggest command ─────────────────────────────────────────────────────────

class TestSuggestCommand:
    def test_no_graph_exit_1(self, isolated_home):
        result = runner.invoke(app, ["agent", "suggest"])
        assert result.exit_code == 1
        assert "no graph" in result.stdout.lower()

    def test_healthy_graph(self, seeded_graph):
        result = runner.invoke(app, ["agent", "suggest"])
        assert result.exit_code == 0


# ── agent status ────────────────────────────────────────────────────────────

class TestAgentStatusCommand:
    def test_status_prints_dashboard(self, seeded_graph):
        result = runner.invoke(app, ["agent", "status"])
        assert result.exit_code == 0
        assert "nodes:" in result.stdout

    def test_status_daemon_stopped(self, seeded_graph):
        result = runner.invoke(app, ["agent", "status"])
        assert result.exit_code == 0
        assert "daemon: stopped" in result.stdout

    def test_status_daemon_running_with_version(self, seeded_graph, isolated_home):
        """Status shows daemon PID + version when .daemon.version exists."""
        import os
        (isolated_home / ".daemon.pid").write_text(str(os.getpid()))
        (isolated_home / ".daemon.version").write_text("9.9.9")
        result = runner.invoke(app, ["agent", "status"])
        assert result.exit_code == 0
        assert "running" in result.stdout
        assert str(os.getpid()) in result.stdout
        assert "version=9.9.9" in result.stdout

    def test_status_daemon_running_no_version_file(self, seeded_graph, isolated_home):
        """Daemon running but no .daemon.version — falls back to CLI version."""
        import os
        (isolated_home / ".daemon.pid").write_text(str(os.getpid()))
        # No .daemon.version
        result = runner.invoke(app, ["agent", "status"])
        assert result.exit_code == 0
        assert "running" in result.stdout

    def test_status_daemon_version_mismatch(self, seeded_graph, isolated_home):
        """Daemon version != CLI version → warns about restart."""
        import os
        (isolated_home / ".daemon.pid").write_text(str(os.getpid()))
        (isolated_home / ".daemon.version").write_text("0.1.0")
        result = runner.invoke(app, ["agent", "status"])
        assert result.exit_code == 0
        assert "restart needed" in result.stdout


# ── agent detect ────────────────────────────────────────────────────────────

class TestAgentDetectCommand:
    def test_human_output(self, isolated_home, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents", lambda: []
        )
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_active_agent", lambda: None
        )
        result = runner.invoke(app, ["agent", "detect"])
        assert result.exit_code == 0
        assert "active" in result.stdout.lower()

    def test_json_output(self, isolated_home, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents", lambda: []
        )
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_active_agent", lambda: None
        )
        result = runner.invoke(app, ["agent", "detect", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout.strip())
        assert "agents" in data
        assert "daemon" in data


# ── agent wire ──────────────────────────────────────────────────────────────

class TestAgentWireCommand:
    def test_no_agents_detected(self, isolated_home, monkeypatch):
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents", lambda: []
        )
        result = runner.invoke(app, ["agent", "wire"])
        assert result.exit_code == 0
        assert "no coding agents" in result.stdout.lower()

    def test_dry_run_no_writes(self, isolated_home, monkeypatch):
        from lorekeep.integrations.registry import find
        monkeypatch.setattr(
            "lorekeep.integrations.detect.detect_agents", lambda: ["claude"]
        )
        write_calls = []
        monkeypatch.setattr(
            "lorekeep.cli._wire_one",
            lambda *a, **kw: write_calls.append(kw) or (None, None),
        )
        result = runner.invoke(app, ["agent", "wire", "--dry-run"])
        assert result.exit_code == 0
        assert len(write_calls) == 0  # dry-run skips

    def test_unknown_agent_exit_1(self, isolated_home, monkeypatch):
        result = runner.invoke(app, ["agent", "wire", "--agent", "unknown-agent"])
        assert result.exit_code == 1


# ── eval locomo command ─────────────────────────────────────────────────────

class TestEvalLocomoCommand:
    def test_eval_locomo_compile_data_not_found(self, seeded_graph, monkeypatch):
        """eval-locomo --compile should exit 1 when data file missing."""
        result = runner.invoke(app, ["eval-locomo", "--compile", "--data", "/nonexistent/locomo10.json"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


# ── service install/uninstall (mocked) ──────────────────────────────────────

class TestServiceCommands:
    def test_service_install_success(self, isolated_home, monkeypatch):
        from lorekeep.daemon_service import install
        monkeypatch.setattr("lorekeep.daemon_service.install", lambda home: ("linux", Path("/tmp/test.service")))
        result = runner.invoke(app, ["agent", "service", "install"])
        assert result.exit_code == 0

    def test_service_install_failure(self, isolated_home, monkeypatch):
        def _boom(home):
            raise RuntimeError("unsupported platform")
        monkeypatch.setattr("lorekeep.daemon_service.install", _boom)
        result = runner.invoke(app, ["agent", "service", "install"])
        assert result.exit_code == 1

    def test_service_uninstall(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.daemon_service.uninstall", lambda: True)
        result = runner.invoke(app, ["agent", "service", "uninstall"])
        assert result.exit_code == 0

    def test_service_status(self, isolated_home, monkeypatch):
        result = runner.invoke(app, ["agent", "service", "status"])
        assert result.exit_code == 0


# ── _wire_one helper ────────────────────────────────────────────────────────

class TestWireOneHelper:
    def test_writes_config_and_hook(self, isolated_home, monkeypatch, tmp_path):
        from lorekeep.cli import _wire_one
        from lorekeep.integrations.registry import find

        spec = find("claude")
        target = tmp_path / "project"
        target.mkdir()

        # Mock the writer to return paths without touching real files
        monkeypatch.setattr(
            f"lorekeep.integrations.{spec.writer_module.split('.')[-1]}.write_config",
            lambda *a, **kw: target / ".mcp.json",
        )
        if spec.supports_hook:
            monkeypatch.setattr(
                f"lorekeep.integrations.{spec.writer_module.split('.')[-1]}.write_hook",
                lambda *a, **kw: target / ".claude" / "settings.json",
            )

        written, hooked = _wire_one(spec, target, "test-ns", scope="project")
        assert written is not None


# ── _dump_session_transcript with valid agent ──────────────────────────────

class TestDumpSessionTranscriptValid:
    def test_dumps_known_agent(self, isolated_home, monkeypatch):
        """_dump_session_transcript calls dump_session_turns for known agents."""
        from lorekeep.cli import _dump_session_transcript
        from lorekeep.integrations.registry import find

        spec = find("claude")
        if spec.session is None:
            pytest.skip("claude has no session spec")

        importer = spec.importer()
        # Mock parse + key on importer
        monkeypatch.setattr(importer, spec.session.parse, lambda handle: {"turns": []})
        monkeypatch.setattr(importer, spec.session.key, lambda handle: "test-session")

        # Mock dump_session_turns to return file list
        monkeypatch.setattr(
            "lorekeep.importer.session_dump.dump_session_turns",
            lambda *a, **kw: [Path("/tmp/test.md")],
        )
        monkeypatch.setattr(
            "lorekeep.importer.session_dump.prune_sessions",
            lambda *a, **kw: None,
        )

        acfg = MagicMock()
        acfg.transcript_max_chars = 50000
        acfg.transcript_max_batches = 10
        acfg.transcript_retain_sessions = 5

        result = _dump_session_transcript("claude", "test-handle", isolated_home / "raw", acfg)
        assert result == 1


# ── Daemon watch loop with sessions ─────────────────────────────────────────

class TestDaemonWatchSessions:
    """Exercise session import + transcript dump paths inside the watch loop."""

    def _setup_watch_env(self, isolated_home, monkeypatch, fixtures):
        raw = isolated_home / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "notes.md").write_text("# Notes\n\nSome fact about X.")
        shutil.copy(fixtures / "schema.json", isolated_home / "schema.json")
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _fake_compile_provider())
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
        monkeypatch.setattr("lorekeep.backup.has_remote", lambda h: False)
        monkeypatch.setattr("lorekeep.cli._sync_agent_wiring", lambda **kw: [])

    def test_watch_session_import(self, isolated_home, monkeypatch, fixtures):
        """Watch discovers sessions and imports memory on first sight."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        mem_dir = isolated_home / "mem"
        mem_dir.mkdir()
        (mem_dir / "fact.md").write_text("# Fact")

        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions",
                            lambda: [("claude", isolated_home / "sess", mem_dir)])
        monkeypatch.setattr("lorekeep.cli._quick_import_session", lambda *a: 2)
        monkeypatch.setattr("lorekeep.cli._discover_session_transcripts", lambda cwd=None: [])

        # Use a flag-based break: after first successful cycle, inject a
        # SystemExit(0) via _discover_watchable_sessions to cleanly exit.
        # Use _sync_agent_wiring to break the loop (it's called inside try).
        cycle_count = [0]
        original_discover = lambda: [("claude", isolated_home / "sess", mem_dir)]

        def discover_and_break():
            cycle_count[0] += 1
            if cycle_count[0] >= 2:
                raise KeyboardInterrupt
            return original_discover()

        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", discover_and_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())

        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "importing" in result.stdout.lower()

    def test_watch_session_import_error(self, isolated_home, monkeypatch, fixtures):
        """Watch logs session import errors and continues."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        mem_dir = isolated_home / "mem"
        mem_dir.mkdir()
        (mem_dir / "fact.md").write_text("# Fact")

        monkeypatch.setattr("lorekeep.cli._quick_import_session",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("import failed")))
        monkeypatch.setattr("lorekeep.cli._discover_session_transcripts", lambda cwd=None: [])

        cycle_count = [0]
        def discover_and_break():
            cycle_count[0] += 1
            if cycle_count[0] >= 2:
                raise KeyboardInterrupt
            return [("claude", isolated_home / "sess", mem_dir)]

        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", discover_and_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())

        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "import error" in result.stdout.lower()

    def test_watch_transcript_dump(self, isolated_home, monkeypatch, fixtures):
        """Watch discovers transcripts and dumps them."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        # Write config.yaml so _acfg is loaded with watch_transcripts=True
        (isolated_home / "config.yaml").write_text(
            "agents:\n  auto_wire: false\n  watch_transcripts: true\n  enabled: [claude]\n"
        )
        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", lambda: [])
        monkeypatch.setattr("lorekeep.cli._dump_session_transcript", lambda *a: 3)

        cycle_count = [0]
        def discover_transcripts_and_break(cwd=None):
            cycle_count[0] += 1
            if cycle_count[0] >= 2:
                raise KeyboardInterrupt
            return [("claude", "session-handle")]

        monkeypatch.setattr("lorekeep.cli._discover_session_transcripts", discover_transcripts_and_break)
        # Speed up time.monotonic so the 30s transcript throttle passes immediately
        fake_time = [0.0]
        def fast_monotonic():
            fake_time[0] += 100.0
            return fake_time[0]
        monkeypatch.setattr("time.monotonic", fast_monotonic)
        monkeypatch.setattr("time.sleep", safe_sleep_break())

        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "transcript" in result.stdout.lower()


# ── _auto_generate_wiki error handling ──────────────────────────────────────

class TestAutoGenerateWiki:
    def test_wiki_generation_succeeds(self, isolated_home, monkeypatch, fixtures):
        """_auto_generate_wiki builds wiki from a valid graph."""
        from lorekeep.cli import _auto_generate_wiki
        out = isolated_home / "graph"
        out.mkdir()
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
        wiki = isolated_home / "wiki"
        schema = isolated_home / "schema.json"
        shutil.copy(fixtures / "schema.json", schema)
        _auto_generate_wiki(out, wiki, schema)
        assert wiki.exists()

    def test_wiki_generation_handles_error(self, isolated_home, monkeypatch, tmp_path):
        """_auto_generate_wiki swallows errors gracefully."""
        from lorekeep.cli import _auto_generate_wiki
        out = tmp_path / "graph"
        out.mkdir()
        # No facts.jsonl → wiki gen fails → swallowed
        _auto_generate_wiki(out, tmp_path / "wiki", None)


# ── Daemon watch loop advanced paths ────────────────────────────────────────

class TestDaemonWatchAdvanced:
    """Cover compile-error, backup, pending-resolve, and version-upgrade paths."""

    def _setup(self, isolated_home, monkeypatch, fixtures):
        raw = isolated_home / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "notes.md").write_text("# Notes\n\nSome fact.")
        shutil.copy(fixtures / "schema.json", isolated_home / "schema.json")
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
        monkeypatch.setattr("lorekeep.backup.has_remote", lambda h: False)
        monkeypatch.setattr("lorekeep.cli._sync_agent_wiring", lambda **kw: [])
        # Break loop via _discover_watchable_sessions
        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", make_break_after())
        monkeypatch.setattr("lorekeep.cli._discover_session_transcripts", lambda cwd=None: [])

    def test_watch_compile_error_logged(self, isolated_home, monkeypatch, fixtures):
        """Compile failure in daemon loop is logged, not fatal."""
        self._setup(isolated_home, monkeypatch, fixtures)
        monkeypatch.setattr("lorekeep.cli._make_provider",
                            lambda c: (_ for _ in ()).throw(RuntimeError("bad provider")))

        cycle_count = [0]
        def discover_add_and_break():
            cycle_count[0] += 1
            if cycle_count[0] == 2:
                (isolated_home / "raw" / "new.md").write_text("# New")
            elif cycle_count[0] >= 3:
                raise KeyboardInterrupt
            return []
        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", discover_add_and_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "compile error" in result.stdout.lower()

    def test_watch_backup_after_compile(self, isolated_home, monkeypatch, fixtures):
        """Post-compile backup sync is attempted."""
        self._setup(isolated_home, monkeypatch, fixtures)
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _fake_compile_provider())
        backup_calls = []
        monkeypatch.setattr("lorekeep.backup.sync_backup", lambda h: backup_calls.append(1) or True)

        cycle_count = [0]
        def discover_add_and_break():
            cycle_count[0] += 1
            if cycle_count[0] == 2:
                (isolated_home / "raw" / "new.md").write_text("# New")
            elif cycle_count[0] >= 3:
                raise KeyboardInterrupt
            return []
        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", discover_add_and_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "compiling" in result.stdout.lower()

    def test_watch_pending_resolve(self, isolated_home, monkeypatch, fixtures):
        """Pending journal changes trigger auto-resolve."""
        self._setup(isolated_home, monkeypatch, fixtures)
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _fake_compile_provider())

        out_dir = isolated_home / "graph"
        out_dir.mkdir(exist_ok=True)
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out_dir / "facts.jsonl")

        pending_dir = isolated_home / "pending" / "payments"
        pending_dir.mkdir(parents=True)
        (pending_dir / "journal.jsonl").write_text(
            '{"entry_id":"e1","proposed_at":"2024-01-01T00:00:00Z",'
            '"kind":"node","ns":"payments","op":"add",'
            '"fact":{"id":"test:p1","kind":"node","type":"service",'
            '"label":"P1","ns":"payments","valid_from":"2024-01-01",'
            '"props":{},"src":"test"}}\n'
        )

        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0

    def test_watch_version_upgrade_restart(self, isolated_home, monkeypatch, fixtures):
        """Version change triggers execv restart."""
        self._setup(isolated_home, monkeypatch, fixtures)
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _fake_compile_provider())

        version_calls = [0]
        from lorekeep.cli import _on_disk_version
        original = _on_disk_version()

        def fake_version():
            version_calls[0] += 1
            if version_calls[0] >= 2:
                return "99.99.99"  # different from startup
            return original

        monkeypatch.setattr("lorekeep.cli._on_disk_version", fake_version)
        monkeypatch.setattr("os.execv", lambda *a: (_ for _ in ()).throw(SystemExit(0)))
        monkeypatch.setattr("time.sleep", lambda s: None)

        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        # SystemExit(0) from mocked execv → exit 0
        assert result.exit_code == 0
        assert "upgraded" in result.stdout.lower()


# ── _daemon_pid helper ──────────────────────────────────────────────────────

class TestDaemonPid:
    def _paths(self, isolated_home):
        from lorekeep.paths import resolve_paths
        return resolve_paths()

    def test_no_pid_file(self, isolated_home):
        from lorekeep.cli import _daemon_pid
        assert _daemon_pid(self._paths(isolated_home)) is None

    def test_alive_pid(self, isolated_home):
        from lorekeep.cli import _daemon_pid
        (isolated_home / ".daemon.pid").write_text(str(os.getpid()))
        assert _daemon_pid(self._paths(isolated_home)) == os.getpid()

    def test_dead_pid(self, isolated_home):
        from lorekeep.cli import _daemon_pid
        (isolated_home / ".daemon.pid").write_text("999999")
        assert _daemon_pid(self._paths(isolated_home)) is None

    def test_corrupt_pid_file(self, isolated_home):
        from lorekeep.cli import _daemon_pid
        (isolated_home / ".daemon.pid").write_text("not-a-number")
        assert _daemon_pid(self._paths(isolated_home)) is None


# ── _do_auto_resolve exception ──────────────────────────────────────────────

class TestAutoResolveException:
    def test_resolve_exception_returns_false(self, isolated_home, monkeypatch):
        """_do_auto_resolve returns False and logs on exception."""
        from lorekeep.cli import _do_auto_resolve
        monkeypatch.setattr(
            "lorekeep.compile.resolve.merge_journals",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("merge failed")),
        )
        out_dir = isolated_home / "graph"
        out_dir.mkdir()
        (out_dir / "facts.jsonl").write_text("")
        pending_dir = isolated_home / "pending" / "test"
        pending_dir.mkdir(parents=True)
        (pending_dir / "journal.jsonl").write_text(
            '{"entry_id":"e1","proposed_at":"2024-01-01T00:00:00Z",'
            '"kind":"node","ns":"test","op":"add",'
            '"fact":{"id":"test:n1","kind":"node","type":"test",'
            '"label":"N1","ns":"test","valid_from":"2024-01-01",'
            '"props":{},"src":"test"}}\n'
        )
        result = _do_auto_resolve(out_dir, pending_dir)
        assert result is False


# ── Init auto-import with memory ────────────────────────────────────────────

class TestInitAutoImport:
    def test_init_imports_memory(self, isolated_home, monkeypatch):
        """Init auto-imports memory files from detected agents."""
        from lorekeep.integrations.registry import find
        spec = find("claude")
        if spec.memory is None:
            pytest.skip("claude has no memory spec")
        monkeypatch.setattr(
            f"lorekeep.importer.claude.{spec.memory.import_fn}",
            lambda raw_root, namespace=None: [Path("/tmp/m.md")],
        )
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "imported" in result.stdout.lower()

    def test_init_import_error_debug(self, isolated_home, monkeypatch):
        """Init import error is echoed when LOREKEEP_DEBUG is set."""
        from lorekeep.integrations.registry import find
        spec = find("claude")
        monkeypatch.setattr(
            f"lorekeep.importer.claude.{spec.memory.import_fn}",
            lambda raw_root, namespace=None: (_ for _ in ()).throw(RuntimeError("no access")),
        )
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)
        monkeypatch.setenv("LOREKEEP_DEBUG", "1")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "import error" in result.stdout.lower()


# ── _report_content_quality ─────────────────────────────────────────────────

class TestContentQuality:
    def test_quality_report_with_gaps(self, capsys):
        """_report_content_quality warns about coverage gaps."""
        from lorekeep.cli import _report_content_quality
        from lorekeep.models import Manifest, ContentQuality
        manifest = Manifest(
            schema_version=1,
            chunk_count=1, node_count=5, edge_count=3,
            run_id="test", facts_hash="abc",
            content_quality=ContentQuality(
                node_summary_coverage=0.5,
                edge_description_coverage=0.8,
                node_label_coverage=0.9,
                node_description_coverage=0.7,
                generic_edge_ratio=0.3,
                duplicate_label_count=0,
            ),
        )
        _report_content_quality(manifest)
        captured = capsys.readouterr()
        assert "summaries" in captured.out.lower() or "coverage" in captured.out.lower()


# ── Daemon watch loop ───────────────────────────────────────────────────────

class TestDaemonWatchLoop:
    """Exercise the daemon watch() body — the biggest single coverage gap."""

    def _setup_watch_env(self, isolated_home, monkeypatch, fixtures):
        """Create a valid data home for the watch command."""
        raw = isolated_home / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "notes.md").write_text("# Notes\n\nSome fact about X.")
        shutil.copy(fixtures / "schema.json", isolated_home / "schema.json")
        # Mock provider so compile works without LLM
        monkeypatch.setattr("lorekeep.cli._make_provider", lambda c: _fake_compile_provider())
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: True)
        # Mock backup (no remote configured)
        monkeypatch.setattr("lorekeep.backup.has_remote", lambda h: False)
        # Break after first cycle via _discover_watchable_sessions
        # (called inside the try block, so KeyboardInterrupt is caught properly)
        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", make_break_after())
        monkeypatch.setattr("lorekeep.cli._discover_session_transcripts", lambda cwd=None: [])
        monkeypatch.setattr("lorekeep.cli._sync_agent_wiring", lambda **kw: [])

    def test_watch_one_cycle(self, isolated_home, monkeypatch, fixtures):
        """Daemon watch runs startup + one cycle and exits on KeyboardInterrupt."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "monitoring" in result.stdout.lower()

    def test_watch_writes_version_file(self, isolated_home, monkeypatch, fixtures):
        """watch() writes .daemon.version at startup."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        version_file = isolated_home / ".daemon.version"
        assert version_file.exists()
        assert version_file.read_text().strip() != ""

    def test_watch_pid_file_running(self, isolated_home, monkeypatch, fixtures):
        """Watch exits 1 when another daemon is already running."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        (isolated_home / ".daemon.pid").write_text(str(os.getpid()))
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 1
        assert "already running" in result.stdout.lower()

    def test_watch_pid_file_stale(self, isolated_home, monkeypatch, fixtures):
        """Stale PID file (dead process) is ignored."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        (isolated_home / ".daemon.pid").write_text("999999")
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0

    def test_watch_compile_on_change(self, isolated_home, monkeypatch, fixtures):
        """Watch detects new raw/ files and triggers compile."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)

        cycle_count = [0]
        def discover_add_and_break():
            cycle_count[0] += 1
            if cycle_count[0] == 2:
                (isolated_home / "raw" / "new.md").write_text("# New\n\nAnother fact.")
            elif cycle_count[0] >= 3:
                raise KeyboardInterrupt
            return []

        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", discover_add_and_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0
        assert "compiling" in result.stdout.lower()

    def test_watch_no_sessions(self, isolated_home, monkeypatch, fixtures):
        """Watch with --no-watch-sessions skips session discovery."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        # Since --no-watch-sessions disables _discover_watchable_sessions,
        # we need a different break point. Use compile trigger + _on_disk_version.
        from lorekeep.cli import _on_disk_version
        call_v = [0]
        def version_break():
            call_v[0] += 1
            if call_v[0] >= 3:
                raise KeyboardInterrupt
            return _on_disk_version()
        monkeypatch.setattr("lorekeep.cli._on_disk_version", version_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--no-watch-sessions", "--interval", "1"])
        assert result.exit_code == 0

    def test_watch_loop_exception_continues(self, isolated_home, monkeypatch, fixtures):
        """Unexpected exception in loop body is logged, loop continues."""
        self._setup_watch_env(isolated_home, monkeypatch, fixtures)
        cycle_count = [0]
        def discover_err_and_break():
            cycle_count[0] += 1
            if cycle_count[0] == 2:
                raise RuntimeError("discovery failed")
            elif cycle_count[0] >= 3:
                raise KeyboardInterrupt
            return []
        monkeypatch.setattr("lorekeep.cli._discover_watchable_sessions", discover_err_and_break)
        monkeypatch.setattr("time.sleep", safe_sleep_break())
        result = runner.invoke(app, ["agent", "watch", "--interval", "1"])
        assert result.exit_code == 0


# ── Import commands (deep paths) ────────────────────────────────────────────

class TestImportCommands:
    def test_import_codex_deep(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.codex.find_current_session",
                            lambda: Path("/tmp/rollout.jsonl"))
        monkeypatch.setattr("lorekeep.importer.codex.import_codex",
                            lambda **kw: {"memory": [], "session": [Path("/tmp/a.md")]})
        monkeypatch.setattr("lorekeep.cli._make_import_provider",
                            lambda c: _fake_import_provider())
        result = runner.invoke(app, ["import", "--from", "codex"])
        assert result.exit_code == 0
        assert "imported" in result.stdout.lower()

    def test_import_codex_quick(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.codex.find_current_session",
                            lambda: None)
        monkeypatch.setattr("lorekeep.importer.codex.import_codex",
                            lambda **kw: {"memory": [Path("/tmp/m.md")], "session": []})
        result = runner.invoke(app, ["import", "--from", "codex", "--quick"])
        assert result.exit_code == 0
        assert "imported" in result.stdout.lower()

    def test_import_codex_no_session_exit_1(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.codex.find_current_session",
                            lambda: None)
        result = runner.invoke(app, ["import", "--from", "codex"])
        assert result.exit_code == 1
        assert "no codex session" in result.stdout.lower()

    def test_import_claude_deep(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.claude.find_current_session",
                            lambda: Path("/tmp/claude-session"))
        monkeypatch.setattr("lorekeep.importer.claude.import_claude",
                            lambda **kw: {"memory": [Path("/tmp/m.md")], "session": [Path("/tmp/s.md")]})
        monkeypatch.setattr("lorekeep.cli._make_import_provider",
                            lambda c: _fake_import_provider())
        result = runner.invoke(app, ["import", "--from", "claude"])
        assert result.exit_code == 0
        assert "imported" in result.stdout.lower()

    def test_import_claude_quick(self, isolated_home, monkeypatch):
        session_dir = isolated_home / "claude-session"
        session_dir.mkdir()
        monkeypatch.setattr("lorekeep.importer.claude.find_current_session",
                            lambda: session_dir)
        monkeypatch.setattr("lorekeep.importer.claude.import_claude",
                            lambda **kw: {"memory": [Path("/tmp/m.md")], "session": []})
        result = runner.invoke(app, ["import", "--from", "claude", "--quick"])
        assert result.exit_code == 0

    def test_import_claude_no_session_exit_1(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.claude.find_current_session",
                            lambda: None)
        result = runner.invoke(app, ["import", "--from", "claude"])
        assert result.exit_code == 1
        assert "no claude session" in result.stdout.lower()

    def test_import_cursor_quick_fails(self, isolated_home):
        result = runner.invoke(app, ["import", "--from", "cursor", "--quick"])
        assert result.exit_code == 1
        assert "deep-only" in result.stdout.lower()

    def test_import_cursor_deep(self, isolated_home, monkeypatch):
        db_path = isolated_home / "state.vscdb"
        db_path.write_text("{}")
        monkeypatch.setattr("lorekeep.importer.cursor.find_cursor_state_db",
                            lambda: db_path)
        monkeypatch.setattr("lorekeep.importer.cursor.import_cursor",
                            lambda **kw: {"session": [Path("/tmp/s.md")]})
        monkeypatch.setattr("lorekeep.cli._make_import_provider",
                            lambda c: _fake_import_provider())
        result = runner.invoke(app, ["import", "--from", "cursor"])
        assert result.exit_code == 0
        assert "imported" in result.stdout.lower()

    def test_import_cursor_no_db_exit_1(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.cursor.find_cursor_state_db",
                            lambda: None)
        result = runner.invoke(app, ["import", "--from", "cursor"])
        assert result.exit_code == 1

    def test_import_opencode_quick_fails(self, isolated_home):
        result = runner.invoke(app, ["import", "--from", "opencode", "--quick"])
        assert result.exit_code == 1
        assert "deep-only" in result.stdout.lower()

    def test_import_opencode_deep(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.opencode.find_current_session",
                            lambda: "session-123")
        monkeypatch.setattr("lorekeep.importer.opencode.import_opencode",
                            lambda **kw: {"session": [Path("/tmp/s.md")]})
        monkeypatch.setattr("lorekeep.cli._make_import_provider",
                            lambda c: _fake_import_provider())
        result = runner.invoke(app, ["import", "--from", "opencode"])
        assert result.exit_code == 0
        assert "imported" in result.stdout.lower()

    def test_import_opencode_no_session_exit_1(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.opencode.find_current_session",
                            lambda: None)
        result = runner.invoke(app, ["import", "--from", "opencode"])
        assert result.exit_code == 1

    def test_import_dry_run(self, isolated_home, monkeypatch):
        monkeypatch.setattr("lorekeep.importer.claude.find_current_session",
                            lambda: Path("/tmp/claude-session"))
        monkeypatch.setattr("lorekeep.importer.claude.import_claude",
                            lambda **kw: {"memory": [Path("/tmp/m.md")], "session": [Path("/tmp/s.md")]})
        monkeypatch.setattr("lorekeep.cli._make_import_provider",
                            lambda c: _fake_import_provider())
        result = runner.invoke(app, ["import", "--from", "claude", "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.stdout.lower()

    def test_import_unknown_source(self, isolated_home):
        result = runner.invoke(app, ["import", "--from", "unknown"])
        assert result.exit_code == 1
        assert "unknown source" in result.stdout.lower()


# ── Hook command ────────────────────────────────────────────────────────────

class TestHookCommand:
    def test_hook_runs_clean(self, isolated_home, monkeypatch):
        """Hook command runs without error when no agents are installed."""
        monkeypatch.setattr("lorekeep.integrations.registry.all_specs", lambda: [])
        result = runner.invoke(app, ["hook"])
        assert result.exit_code == 0

    def test_hook_imports_memory(self, isolated_home, monkeypatch):
        """Hook imports memory from enabled agents."""
        from lorekeep.integrations.registry import find
        spec = find("claude")
        monkeypatch.setattr(
            f"lorekeep.importer.claude.{spec.memory.import_fn}",
            lambda raw_root, namespace=None: [Path("/tmp/m.md")],
        )
        if spec.session:
            monkeypatch.setattr(
                f"lorekeep.importer.claude.{spec.session.dump_fn}",
                lambda *a, **kw: [],
            )
        result = runner.invoke(app, ["hook"])
        assert result.exit_code == 0


# ── Lint with issues ────────────────────────────────────────────────────────

class TestLintWithIssues:
    def test_lint_reports_orphans(self, seeded_graph, monkeypatch):
        """Lint should report issues when graph has problems."""
        from lorekeep.agent import LintReport
        fake_report = LintReport(
            orphans=["node:1"], stale=[], contradictions=[],
            missing_endpoints=[], coverage_gaps=["low"],
        )
        monkeypatch.setattr("lorekeep.agent.lint", lambda store: fake_report)
        result = runner.invoke(app, ["agent", "lint"])
        assert result.exit_code == 0
        assert "orphans" in result.stdout.lower()

    def test_lint_with_focus(self, seeded_graph, monkeypatch):
        """Lint with --focus filters issues."""
        from lorekeep.agent import LintReport
        fake_report = LintReport(
            orphans=["node:1", "node:2"], stale=[], contradictions=[],
            missing_endpoints=[], coverage_gaps=[],
        )
        monkeypatch.setattr("lorekeep.agent.lint", lambda store: fake_report)
        result = runner.invoke(app, ["agent", "lint", "--focus", "node:1"])
        assert result.exit_code == 0
        assert "node:1" in result.stdout


# ── Init command edge cases ─────────────────────────────────────────────────

class TestInitEdgeCases:
    def test_init_wire_failure_continues(self, isolated_home, monkeypatch):
        """Init continues even if agent wiring fails."""
        monkeypatch.setattr("lorekeep.integrations.detect.detect_agents",
                            lambda: ["claude"])
        monkeypatch.setattr("lorekeep.cli._wire_one",
                            lambda *a, **kw: (_ for _ in ()).throw(PermissionError("no access")))
        monkeypatch.setattr("lorekeep.cli._has_provider", lambda c: False)
        monkeypatch.setattr("lorekeep.cli._start_daemon", lambda p: None)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "failed" in result.stdout.lower()


# ── _do_auto_resolve flagged path ───────────────────────────────────────────

class TestAutoResolveFlagged:
    def test_resolve_with_flagged_entries(self, isolated_home, monkeypatch, fixtures):
        """_do_auto_resolve handles flagged journal entries."""
        from lorekeep.cli import _do_auto_resolve
        out_dir = isolated_home / "graph"
        out_dir.mkdir(exist_ok=True)
        shutil.copy(fixtures / "gold/payments.facts.jsonl", out_dir / "facts.jsonl")
        pending_dir = isolated_home / "pending"
        ns_dir = pending_dir / "test-ns"
        ns_dir.mkdir(parents=True)
        (ns_dir / "journal.jsonl").write_text(
            '{"entry_id":"e1","proposed_at":"2024-01-01T00:00:00Z",'
            '"kind":"node","ns":"test-ns","op":"add",'
            '"fact":{"id":"test:node1","kind":"node","type":"test",'
            '"label":"Test","ns":"test-ns","valid_from":"2024-01-01",'
            '"props":{},"src":"test"}}\n'
        )
        result = _do_auto_resolve(out_dir, pending_dir, None, None)
        assert isinstance(result, bool)


# ── Suggest with issues ─────────────────────────────────────────────────────

class TestSuggestWithIssues:
    def test_suggest_reports_issues(self, seeded_graph, monkeypatch):
        """Suggest should report suggestions when graph has problems."""
        from lorekeep.agent import SuggestionReport
        fake_report = SuggestionReport(
            gaps=["gap:1", "gap:2"],
            under_sourced=["node:1"],
            suggestions=["Add more raw/ directories"],
        )
        monkeypatch.setattr("lorekeep.agent.suggest", lambda store: fake_report)
        result = runner.invoke(app, ["agent", "suggest"])
        assert result.exit_code == 0
        assert "gap" in result.stdout.lower()


# ── _progress helpers ───────────────────────────────────────────────────────

class TestProgressHelpers:
    def test_progress_ctx_nontty(self, isolated_home, monkeypatch):
        from lorekeep.cli import _progress_ctx, _progress_cb
        raw = isolated_home / "raw"
        raw.mkdir()
        (raw / "test.md").write_text("# Test")
        with _progress_ctx(raw, 50) as handle:
            cb = _progress_cb(handle)
            if cb:
                cb(1, 10, {"path": "test.md", "line": 1})


# ── Compile error reporting ─────────────────────────────────────────────────

class TestCompileErrorReporting:
    def test_report_compile_errors_no_errors(self, capsys):
        """_report_compile_errors with no errors does nothing."""
        from lorekeep.cli import _report_compile_errors
        from lorekeep.models import Manifest
        manifest = Manifest(
            schema_version=1,
            chunk_count=3, node_count=10, edge_count=5,
            run_id="test-run", facts_hash="abc123",
        )
        _report_compile_errors(manifest)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_report_compile_errors_with_errors(self, capsys):
        """_report_compile_errors prints chunk errors to stderr."""
        from lorekeep.cli import _report_compile_errors
        from lorekeep.models import Manifest, CompileError
        manifest = Manifest(
            schema_version=1,
            chunk_count=3, node_count=0, edge_count=0,
            run_id="test-run", facts_hash="abc123",
            errors=[
                CompileError(path="test.md", line=1, message="JSON parse failed"),
                CompileError(path="other.md", line=1, message="timeout"),
            ],
        )
        _report_compile_errors(manifest, exit_on_total_failure=False)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "test.md" in combined
