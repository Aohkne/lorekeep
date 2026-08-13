"""Tests for the zero-friction init chain: import → compile → daemon."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(isolated_home):
    """Every test here runs `init`, which auto-wires agents by reading HOME."""


def _setup_env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("LOREKEEP_DEV", "0")
    monkeypatch.chdir(project)
    return home, project


# ── Init chains into compile ──────────────────────────────────────────────


def test_init_compiles_with_provider(patch_make_provider, tmp_path: Path, monkeypatch):
    """init --yes with patched provider should auto-compile facts.jsonl."""
    home, project = _setup_env(tmp_path, monkeypatch)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    facts = home / "graph" / "facts.jsonl"
    assert facts.exists(), f"facts.jsonl not created: {result.stdout}"
    lines = [json.loads(l) for l in facts.read_text().strip().splitlines() if l.strip()]
    assert any(f["id"] == "svc:payments-api" for f in lines)


def test_init_skips_compile_without_api_key(tmp_path: Path, monkeypatch):
    """init --yes without provider key should skip compile gracefully."""
    home, project = _setup_env(tmp_path, monkeypatch)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    assert not (home / "graph" / "facts.jsonl").exists()
    assert "compile" in result.stdout.lower() or "empty" in result.stdout.lower()


# ── Init auto-imports Claude memory ───────────────────────────────────────


def test_init_imports_claude_memory(tmp_path: Path, monkeypatch):
    """init should quick-import Claude memory files if a session is found."""
    home, project = _setup_env(tmp_path, monkeypatch)

    fake_session = tmp_path / "session"
    (fake_session / "memory").mkdir(parents=True)
    (fake_session / "memory" / "note.md").write_text("# Important\nSession knowledge.\n")

    monkeypatch.setattr(
        "lorekeep.importer.claude.find_current_session",
        lambda: fake_session,
    )

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    imported = home / "raw" / "claude-memory" / "note.md"
    assert imported.exists()
    assert "imported" in result.stdout.lower()


# ── Init starts daemon ────────────────────────────────────────────────────


def test_init_starts_daemon(tmp_path: Path, monkeypatch):
    """init --watch in interactive mode should spawn agent watch."""
    home, project = _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_popen = MagicMock(return_value=mock_proc)
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert "agent" in cmd
    assert "watch" in cmd

    pid_file = home / ".daemon.pid"
    assert pid_file.exists()
    assert pid_file.read_text().strip() == "99999"


def test_init_skips_daemon_in_noninteractive(tmp_path: Path, monkeypatch):
    """init --watch in non-interactive mode should not spawn daemon."""
    home, project = _setup_env(tmp_path, monkeypatch)
    # _is_interactive() returns False in CliRunner by default

    mock_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout

    mock_popen.assert_not_called()
    assert "non-interactive" in result.stdout.lower()


def test_init_rerun_revives_dead_daemon(tmp_path: Path, monkeypatch):
    """Re-running init should start daemon if it's not running, even if config exists."""
    home, project = _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr("lorekeep.cli._is_interactive", lambda: True)

    # First init creates config
    runner.invoke(app, ["init", "--yes", "--no-watch"])

    # Simulate dead daemon: stale PID file pointing to nonexistent process
    pid_path = home / ".daemon.pid"
    pid_path.write_text("999999")

    mock_proc = MagicMock()
    mock_proc.pid = 88888
    mock_popen = MagicMock(return_value=mock_proc)
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.stdout

    mock_popen.assert_called_once()
    assert pid_path.read_text().strip() == "88888"


# ── Init creates pending/ dir ─────────────────────────────────────────────


def test_init_creates_pending_dir(tmp_path: Path, monkeypatch):
    """init should create the pending/ directory for journals."""
    home, project = _setup_env(tmp_path, monkeypatch)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    assert (home / "pending").is_dir()


# ── Init idempotent re-run ────────────────────────────────────────────────


def test_init_rerun_skips_chain(tmp_path: Path, monkeypatch):
    """Re-running init on existing config should not re-chain."""
    home, project = _setup_env(tmp_path, monkeypatch)

    result1 = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result1.exit_code == 0

    mock_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    result2 = runner.invoke(app, ["init", "--yes"])
    assert result2.exit_code == 0, result2.stdout

    mock_popen.assert_not_called()
    assert "already initialized" in result2.stdout.lower()


def test_init_rerun_wires_an_agent_installed_later(isolated_home, tmp_path: Path, monkeypatch):
    """Installing an agent after the first init must not require a manual mcp add."""
    home, project = _setup_env(tmp_path, monkeypatch)
    (isolated_home / ".claude").mkdir(parents=True)

    runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert (isolated_home / ".claude.json").exists()
    assert not (isolated_home / ".config" / "opencode" / "opencode.json").exists()

    (isolated_home / ".config" / "opencode").mkdir(parents=True)
    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout
    assert (isolated_home / ".config" / "opencode" / "opencode.json").exists(), f"opencode never wired: {result.stdout}"


def test_init_rerun_does_not_rewrite_unchanged_wiring(isolated_home, tmp_path: Path, monkeypatch):
    """Wiring runs every init, so it must be a true no-op when nothing changed."""
    home, project = _setup_env(tmp_path, monkeypatch)
    (isolated_home / ".claude").mkdir(parents=True)

    runner.invoke(app, ["init", "--yes", "--no-watch"])
    claude_json = isolated_home / ".claude.json"
    before = claude_json.stat().st_mtime_ns

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout
    assert claude_json.stat().st_mtime_ns == before
    assert "already wired" in result.stdout
