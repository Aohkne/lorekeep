"""Lifecycle-hook ingress and native agent wiring tests."""
import json
from pathlib import Path

from typer.testing import CliRunner

import lorekeep.mcp_server as ms
from lorekeep.cli import app
from lorekeep.integrations.claude_code import write_config, write_hook

runner = CliRunner()


# ── lorekeep hook command ─────────────────────────────────────────────────


def test_hook_enqueues_normalized_session_event(tmp_path: Path):
    """The hook process only persists metadata; it never reads transcripts."""
    home = tmp_path / "home"
    home.mkdir()
    payload = {
        "session_id": "session-123",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/tmp/project",
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }

    result = runner.invoke(
        app,
        [
            "hook", "--agent", "claude", "--trigger", "session_end",
            "--home", str(home),
        ],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.stdout

    events = list((home / "hook-events" / "claude").glob("*.json"))
    assert len(events) == 1
    event = json.loads(events[0].read_text())
    assert event["session_id"] == "session-123"
    assert event["transcript_path"] == "/tmp/transcript.jsonl"
    assert event["native_event"] == "SessionEnd"
    assert not (home / "raw").exists()


def test_legacy_hook_without_agent_exits_without_scanning(tmp_path: Path, monkeypatch):
    """Pre-upgrade hook entries fail open until auto-wiring replaces them."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOREKEEP_HOME", str(home))

    result = runner.invoke(app, ["hook"])
    assert result.exit_code == 0
    assert not (home / "hook-events").exists()


def test_hook_rejects_payload_larger_than_bound(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    result = runner.invoke(
        app,
        [
            "hook", "--agent", "claude", "--trigger", "session_end",
            "--home", str(home),
        ],
        input='{"padding":"' + ("x" * (256 * 1024)) + '"}',
    )
    assert result.exit_code == 1
    assert not (home / "hook-events").exists()


def test_hook_coalesces_same_fallback_session(tmp_path: Path):
    """Repeated turn/idle events restart one session's debounce window."""
    home = tmp_path / "home"
    home.mkdir()
    argv = [
        "hook", "--agent", "cmd", "--trigger", "turn_end_fallback",
        "--home", str(home),
    ]
    first = runner.invoke(
        app, argv,
        input=json.dumps({"session_id": "same", "reason": "first"}),
    )
    result2 = runner.invoke(
        app, argv,
        input=json.dumps({"session_id": "same", "reason": "second"}),
    )
    assert first.exit_code == 0
    assert result2.exit_code == 0
    events = list((home / "hook-events" / "cmd").glob("*.json"))
    assert len(events) == 1
    assert json.loads(events[0].read_text())["reason"] == "second"


# ── write_hook (settings.json) ────────────────────────────────────────────


def test_write_hook_creates_settings(tmp_path: Path):
    """write_hook creates .claude/settings.json with SessionEnd hook."""
    cmd = "uvx"
    args = ["lorekeep", "hook"]

    path = write_hook(tmp_path, cmd, args)
    assert path == tmp_path / ".claude" / "settings.json"
    assert path.exists()

    settings = json.loads(path.read_text())
    hooks = settings["hooks"]["SessionEnd"]
    assert len(hooks) == 1
    handler = hooks[0]["hooks"][0]
    assert handler["type"] == "command"
    assert handler["command"] == "uvx"
    assert handler["args"] == ["lorekeep", "hook"]
    assert handler["timeout"] == 30


def test_write_hook_preserves_existing_settings(tmp_path: Path):
    """write_hook preserves existing settings keys."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "permissions": {"allow": ["Read"]},
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
    }))

    write_hook(tmp_path, "uvx", ["lorekeep", "hook"])

    settings = json.loads(settings_path.read_text())
    assert settings["permissions"]["allow"] == ["Read"]
    assert "PreToolUse" in settings["hooks"]
    assert "SessionEnd" in settings["hooks"]


# ── Init wires hook for Claude ─────────────────────────────────────────────


def test_init_writes_claude_hook(tmp_path: Path, monkeypatch):
    """init should write SessionEnd hook when Claude is detected."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    fake_home = tmp_path / "fakehome"
    project.mkdir()
    fake_home.mkdir()
    (fake_home / ".claude").mkdir(parents=True)

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LOREKEEP_DEV", "0")
    monkeypatch.chdir(project)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr("lorekeep.integrations.detect.shutil.which", lambda _: None)
    monkeypatch.delenv("OPENCODE", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    # User-scope default: settings.json goes to ~/.claude/settings.json
    settings = fake_home / ".claude" / "settings.json"
    assert settings.exists(), f"settings.json not written: {result.stdout}"
    data = json.loads(settings.read_text())
    assert "SessionEnd" in data["hooks"]


# ── mcp add wires hook for Claude ──────────────────────────────────────────


def test_mcp_add_writes_claude_hook(tmp_path: Path, monkeypatch):
    """mcp add --agent claude should also write the SessionEnd hook."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--scope", "project", "--read-ns", "backend"])
    assert result.exit_code == 0, result.stdout

    settings = project / ".claude" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    assert "SessionEnd" in data["hooks"]


def test_mcp_add_opencode_writes_hook(tmp_path: Path, monkeypatch):
    """mcp add --agent opencode writes session.idle plugin."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "opencode", "--scope", "project", "--read-ns", "backend"])
    assert result.exit_code == 0, result.stdout

    plugin = project / ".opencode" / "plugins" / "lorekeep.ts"
    assert plugin.exists()
    content = plugin.read_text()
    assert "session.idle" in content
    assert "lorekeep.cli hook" in content


# ── Cursor hook ───────────────────────────────────────────────────────────


def test_write_cursor_hook(tmp_path: Path):
    """write_hook for Cursor creates a project sessionEnd hook."""
    from lorekeep.integrations.cursor import write_hook as write_cursor_hook

    path = write_cursor_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path == tmp_path / ".cursor" / "hooks.json"
    assert path.exists()

    data = json.loads(path.read_text())
    assert data["version"] == 1
    hooks = data["hooks"]["sessionEnd"]
    assert len(hooks) == 1
    assert hooks[0]["command"] == "uvx lorekeep hook"
    assert hooks[0]["timeout"] == 30


def test_mcp_add_cursor_project_hook(tmp_path: Path, monkeypatch):
    """Cursor's IDE-local sessionEnd hook supports project scope."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "cursor", "--scope", "project", "--read-ns", "backend"])
    assert result.exit_code == 0, result.stdout

    hooks_path = project / ".cursor" / "hooks.json"
    assert hooks_path.exists()
    data = json.loads(hooks_path.read_text())
    assert "sessionEnd" in data["hooks"]


def test_mcp_add_copilot_project_scope_reports_user_only_capture(
    tmp_path: Path, isolated_home: Path, monkeypatch,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, [
        "mcp", "add", "--agent", "copilot", "--scope", "project",
    ])

    assert result.exit_code == 0, result.stdout
    assert "use --scope user" in result.stdout
    assert not (project / ".github" / "hooks").exists()


# ── Codex hook ────────────────────────────────────────────────────────────


def test_write_codex_hook(tmp_path: Path):
    """write_hook for Codex creates its bounded SessionEnd hook."""
    from lorekeep.integrations.codex import write_hook as write_codex_hook

    path = write_codex_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path == tmp_path / ".codex" / "hooks.json"
    assert path.exists()

    data = json.loads(path.read_text())
    hooks = data["hooks"]["SessionEnd"]
    assert len(hooks) == 1
    handler = hooks[0]["hooks"][0]
    assert handler["type"] == "command"
    assert "lorekeep hook" in handler["command"]
    assert handler["timeout"] == 3


def test_mcp_add_codex_hook(tmp_path: Path, monkeypatch):
    """mcp add --agent codex writes SessionEnd hook."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    home.mkdir()
    (home / "config.yaml").write_text("install_source: pypi\n")

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["mcp", "add", "--agent", "codex", "--scope", "project", "--read-ns", "backend"])
    assert result.exit_code == 0, result.stdout

    hooks_path = project / ".codex" / "hooks.json"
    assert hooks_path.exists()
    assert "SessionEnd" in json.loads(hooks_path.read_text())["hooks"]


# ── opencode hook ─────────────────────────────────────────────────────────


def test_write_opencode_hook(tmp_path: Path):
    """write_hook for opencode creates .opencode/plugins/lorekeep.ts."""
    from lorekeep.integrations.opencode import write_hook as write_oc_hook

    path = write_oc_hook(tmp_path, "uvx", ["lorekeep", "hook"])
    assert path == tmp_path / ".opencode" / "plugins" / "lorekeep.ts"
    assert path.exists()

    content = path.read_text()
    assert "session.idle" in content
    assert "uvx lorekeep hook" in content
