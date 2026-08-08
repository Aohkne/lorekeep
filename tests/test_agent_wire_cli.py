"""Tests for `lorekeep agent detect` and `lorekeep agent wire`.

Both commands touch user-scope targets — on a real machine that means
``~/.claude.json``, which Claude Code writes live and which holds the user's
OAuth account. Every test here runs under ``isolated_home`` for that reason,
and the ones that matter most are the ones asserting nothing was written.
"""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lorekeep.cli import app
from lorekeep.integrations.registry import AGENT_NAMES

runner = CliRunner()


@pytest.fixture
def wired_project(isolated_home: Path, tmp_path: Path, monkeypatch) -> Path:
    """A CWD with a lorekeep config, and every agent 'installed' under HOME."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("LOREKEEP_HOME", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.yaml").write_text("install_source: local\n")
    for marker in (".claude", ".codex", ".cursor", ".config/opencode"):
        (isolated_home / marker).mkdir(parents=True, exist_ok=True)
    return project


class TestAgentDetect:
    def test_reports_every_agent_not_just_the_active_one(self, wired_project):
        result = runner.invoke(app, ["agent", "detect"])
        assert result.exit_code == 0, result.stdout
        for name in AGENT_NAMES:
            assert name in result.stdout
        assert "active: none" in result.stdout
        assert "daemon: not running" in result.stdout
        assert "session data" in result.stdout

    def test_active_agent_names_the_env_var_that_gave_it_away(
        self, wired_project, monkeypatch
    ):
        monkeypatch.setenv("CLAUDECODE", "1")
        result = runner.invoke(app, ["agent", "detect"])
        assert result.exit_code == 0, result.stdout
        assert "active: claude (CLAUDECODE)" in result.stdout

    def test_exits_zero_with_no_agents_installed(
        self, isolated_home: Path, tmp_path: Path, monkeypatch
    ):
        """A report, not a check — `doctor` is the command that fails."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LOREKEEP_HOME", str(tmp_path / "data"))
        result = runner.invoke(app, ["agent", "detect"])
        assert result.exit_code == 0, result.stdout
        assert "no coding agents found" in result.stdout

    def test_json_shape(self, wired_project):
        result = runner.invoke(app, ["agent", "detect", "--json"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["active"] is None
        assert data["scope"] == "user"
        assert data["daemon"] == {"running": False, "pid": None}
        assert [a["name"] for a in data["agents"]] == list(AGENT_NAMES)
        claude = next(a for a in data["agents"] if a["name"] == "claude")
        assert claude["installed"] is True
        assert claude["wired"] is False
        assert claude["config"].endswith(".claude.json")
        assert claude["ingest"] == ["memory", "transcript"]
        cursor = next(a for a in data["agents"] if a["name"] == "cursor")
        assert cursor["ingest"] == ["transcript"], "cursor authors no memory files"

    def test_installed_but_excluded_agent_is_called_out(self, wired_project, tmp_path):
        """An agent wired but absent from agents.enabled contributes nothing."""
        (tmp_path / "data" / "config.yaml").write_text(
            "install_source: local\nagents:\n  enabled: [claude]\n"
        )
        result = runner.invoke(app, ["agent", "detect"])
        assert result.exit_code == 0, result.stdout
        assert "excluded by agents.enabled" in result.stdout
        assert "codex" in result.stdout.split("note:")[1]

    def test_bad_wire_scope_in_config_is_actionable(self, wired_project, tmp_path):
        (tmp_path / "data" / "config.yaml").write_text(
            "install_source: local\nagents:\n  wire_scope: global\n"
        )
        result = runner.invoke(app, ["agent", "detect"])
        assert result.exit_code == 1
        assert "config set agents.wire_scope" in result.output


class TestAgentWire:
    def test_wires_every_detected_agent_at_user_scope(self, wired_project, isolated_home):
        result = runner.invoke(app, ["agent", "wire"])
        assert result.exit_code == 0, result.stdout
        data = json.loads((isolated_home / ".claude.json").read_text())
        assert data["mcpServers"]["lorekeep"]["command"] == "lorekeep"
        assert "[mcp_servers.lorekeep]" in (
            isolated_home / ".codex" / "config.toml"
        ).read_text()
        assert (isolated_home / ".cursor" / "mcp.json").is_file()
        assert (isolated_home / ".config" / "opencode" / "opencode.json").is_file()
        for name in AGENT_NAMES:
            assert f"{name}: wired ->" in result.stdout

    def test_second_run_reports_unchanged_without_touching_mtime(
        self, wired_project, isolated_home
    ):
        """The anti-churn guarantee the daemon relies on to run this every cycle."""
        runner.invoke(app, ["agent", "wire"])
        targets = [
            isolated_home / ".claude.json",
            isolated_home / ".codex" / "config.toml",
            isolated_home / ".cursor" / "mcp.json",
            isolated_home / ".config" / "opencode" / "opencode.json",
        ]
        before = {t: t.stat().st_mtime_ns for t in targets}

        result = runner.invoke(app, ["agent", "wire"])
        assert result.exit_code == 0, result.stdout
        for name in AGENT_NAMES:
            assert f"{name}: unchanged ->" in result.stdout
        for target, mtime in before.items():
            assert target.stat().st_mtime_ns == mtime, f"{target} was rewritten"

    def test_dry_run_writes_nothing(self, wired_project, isolated_home):
        result = runner.invoke(app, ["agent", "wire", "--dry-run"])
        assert result.exit_code == 0, result.stdout
        assert "would write" in result.stdout
        assert not (isolated_home / ".claude.json").exists()
        assert not (isolated_home / ".cursor" / "mcp.json").exists()

    def test_dry_run_distinguishes_already_wired_targets(
        self, wired_project, isolated_home
    ):
        runner.invoke(app, ["agent", "wire", "--agent", "claude"])
        result = runner.invoke(app, ["agent", "wire", "--dry-run"])
        assert result.exit_code == 0, result.stdout
        lines = result.stdout.splitlines()
        assert any("claude: config" in ln and "already wired" in ln for ln in lines)
        assert any("cursor: config" in ln and "would write" in ln for ln in lines)

    def test_single_agent_flag_wires_only_that_agent(self, wired_project, isolated_home):
        result = runner.invoke(app, ["agent", "wire", "--agent", "codex"])
        assert result.exit_code == 0, result.stdout
        assert (isolated_home / ".codex" / "config.toml").is_file()
        assert not (isolated_home / ".claude.json").exists()

    def test_project_scope_writes_into_cwd(self, wired_project, isolated_home):
        result = runner.invoke(
            app, ["agent", "wire", "--agent", "claude", "--scope", "project"]
        )
        assert result.exit_code == 0, result.stdout
        assert (wired_project / ".mcp.json").is_file()
        assert not (isolated_home / ".claude.json").exists()

    def test_ns_reaches_the_written_config(self, wired_project, isolated_home):
        result = runner.invoke(app, ["agent", "wire", "--agent", "claude", "--ns", "teams/backend"])
        assert result.exit_code == 0, result.stdout
        data = json.loads((isolated_home / ".claude.json").read_text())
        assert data["mcpServers"]["lorekeep"]["env"]["LOREKEEP_NS"] == "teams/backend"

    def test_configured_full_mcp_profile_reaches_agent(
        self, wired_project, tmp_path, isolated_home,
    ):
        (tmp_path / "data" / "config.yaml").write_text(
            "install_source: local\nagents:\n  mcp_profile: full\n"
        )

        result = runner.invoke(app, ["agent", "wire", "--agent", "claude"])

        assert result.exit_code == 0, result.stdout
        data = json.loads((isolated_home / ".claude.json").read_text())
        assert data["mcpServers"]["lorekeep"]["args"][-2:] == [
            "--profile", "full",
        ]

    def test_config_disabled_agent_is_skipped(self, wired_project, tmp_path, isolated_home):
        (tmp_path / "data" / "config.yaml").write_text(
            "install_source: local\nagents:\n  enabled: [claude]\n"
        )
        result = runner.invoke(app, ["agent", "wire"])
        assert result.exit_code == 0, result.stdout
        assert (isolated_home / ".claude.json").is_file()
        assert not (isolated_home / ".codex" / "config.toml").exists()

    def test_explicit_agent_overrides_the_config_filter(
        self, wired_project, tmp_path, isolated_home
    ):
        """An explicit --agent is the user in front of the terminal; it wins."""
        (tmp_path / "data" / "config.yaml").write_text(
            "install_source: local\nagents:\n  enabled: [claude]\n"
        )
        result = runner.invoke(app, ["agent", "wire", "--agent", "codex"])
        assert result.exit_code == 0, result.stdout
        assert (isolated_home / ".codex" / "config.toml").is_file()

    def test_nothing_detected_says_so_without_failing(
        self, isolated_home: Path, tmp_path: Path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LOREKEEP_HOME", str(tmp_path / "data"))
        result = runner.invoke(app, ["agent", "wire"])
        assert result.exit_code == 0, result.stdout
        assert "no coding agents detected" in result.stdout

    def test_force_wires_undetected_agents(
        self, isolated_home: Path, tmp_path: Path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LOREKEEP_HOME", str(tmp_path / "data"))
        result = runner.invoke(app, ["agent", "wire", "--force"])
        assert result.exit_code == 0, result.stdout
        assert (isolated_home / ".claude.json").is_file()

    def test_unknown_agent_exits_one(self, wired_project):
        result = runner.invoke(app, ["agent", "wire", "--agent", "bogus"])
        assert result.exit_code == 1
        assert "unknown agent" in result.stdout

    def test_unknown_scope_exits_one(self, wired_project):
        result = runner.invoke(app, ["agent", "wire", "--scope", "global"])
        assert result.exit_code == 1
        assert "unknown scope" in result.stdout

    def test_one_failing_agent_fails_the_command_but_wires_the_rest(
        self, wired_project, isolated_home, monkeypatch
    ):
        monkeypatch.setattr(
            "lorekeep.integrations.cursor.write_config",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only")),
        )
        result = runner.invoke(app, ["agent", "wire"])
        assert result.exit_code == 1
        assert "cursor: failed (PermissionError" in result.stdout
        assert (isolated_home / ".claude.json").is_file()
