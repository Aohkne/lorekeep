from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_mcp_add_claude_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--scope", "project", "--read-ns", "teams/backend"])
    assert result.exit_code == 0, result.stdout
    import json
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["lorekeep"]["command"] == "lorekeep"
    assert "lorekeep knowledge base" in result.stdout.lower()   # snippet printed


def test_mcp_add_codex_user(isolated_home: Path, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: git+https://github.com/x/lorekeep.git\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "codex", "--scope", "user"])
    assert result.exit_code == 0, result.stdout
    text = (isolated_home / ".codex" / "config.toml").read_text()
    assert "--from" in text and "git+https://github.com/x/lorekeep.git" in text
    assert not (isolated_home / "config.toml").exists()        # the old wrong target
    assert (isolated_home / ".codex" / "hooks.json").exists()


def test_mcp_add_claude_user_writes_claude_json(isolated_home: Path, tmp_path: Path, monkeypatch):
    """Claude Code reads user-scope MCP servers from ~/.claude.json."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--scope", "user"])
    assert result.exit_code == 0, result.stdout
    import json
    data = json.loads((isolated_home / ".claude.json").read_text())
    assert data["mcpServers"]["lorekeep"]["command"] == "lorekeep"
    assert "LOREKEEP_READ_NS" not in data["mcpServers"]["lorekeep"]["env"]
    assert not (isolated_home / ".mcp.json").exists()          # the old wrong target
    assert not (tmp_path / ".mcp.json").exists()               # user scope, not project


def test_mcp_add_second_run_reports_unchanged(tmp_path: Path, monkeypatch):
    """Re-running mcp add must not churn a config that already says the right thing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    runner.invoke(app, ["mcp", "add", "--agent", "claude", "--scope", "project", "--read-ns", "teams/backend"])
    before = (tmp_path / ".mcp.json").stat().st_mtime_ns
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--scope", "project", "--read-ns", "teams/backend"])
    assert result.exit_code == 0, result.stdout
    assert "unchanged" in result.stdout
    assert (tmp_path / ".mcp.json").stat().st_mtime_ns == before


def test_mcp_add_unknown_scope(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--scope", "global"])
    assert result.exit_code == 1
    assert "unknown scope" in result.stdout


def test_mcp_add_opencode_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "opencode", "--scope", "project", "--read-ns", "teams/backend"])
    assert result.exit_code == 0, result.stdout
    import json
    data = json.loads((tmp_path / "opencode.json").read_text())
    entry = data["mcp"]["lorekeep"]
    assert entry["type"] == "local"
    assert entry["command"] == [
        "lorekeep", "serve", "--transport", "stdio",
    ]
    assert entry["environment"]["LOREKEEP_READ_NS"] == "teams/backend"


def test_mcp_add_unknown_agent(tmp_path: Path, monkeypatch):
    """mcp add with an unknown agent exits with error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "bogus", "--read-ns", "x"])
    assert result.exit_code == 1
    assert "unknown agent" in result.stdout


def test_mcp_add_defaults_to_user_scope(isolated_home: Path, tmp_path: Path, monkeypatch):
    """mcp add without --scope uses agents.wire_scope (default: user)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude"])
    assert result.exit_code == 0, result.stdout
    assert (isolated_home / ".claude.json").exists()
    assert not (tmp_path / ".mcp.json").exists()
