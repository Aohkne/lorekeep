"""Init defaults to user-scope wiring (from agents.wire_scope).

The config default is ``wire_scope: user``; init, mcp add, agent wire, and the
daemon must all honor it so the user never gets project-scope files they didn't
ask for.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lorekeep.cli import app

runner = CliRunner()


def test_init_defaults_to_user_scope(isolated_home, tmp_path: Path, monkeypatch):
    """Fresh init with default config writes to user-scope files, not project."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    (isolated_home / ".claude").mkdir(parents=True)

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    # User-scope target
    assert (isolated_home / ".claude.json").exists()
    # Project-scope target must NOT exist
    assert not (project / ".mcp.json").exists()


def test_init_respects_wire_scope_project(isolated_home, tmp_path: Path, monkeypatch):
    """Config with wire_scope: project → project-scope files in cwd."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    (isolated_home / ".claude").mkdir(parents=True)

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    # First init to create the config
    runner.invoke(app, ["init", "--yes", "--no-watch"])

    # Override scope to project
    config_path = home / "config.yaml"
    config_text = config_path.read_text()
    config_text = config_text.replace("wire_scope: user", "wire_scope: project")
    config_path.write_text(config_text)

    # Remove the user-scope file so we can verify project-scope only
    (isolated_home / ".claude.json").unlink(missing_ok=True)

    # Remove the project file too so we get a clean re-wire
    (project / ".mcp.json").unlink(missing_ok=True)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    # Project-scope target
    assert (project / ".mcp.json").exists()
    # User-scope target must NOT exist
    assert not (isolated_home / ".claude.json").exists()


def test_init_no_project_config_files_at_user_scope(isolated_home, tmp_path: Path, monkeypatch):
    """At default user scope, none of the project-scope files appear in cwd."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    (isolated_home / ".claude").mkdir(parents=True)
    (isolated_home / ".cursor").mkdir(parents=True)
    (isolated_home / ".qoder").mkdir(parents=True)

    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["init", "--yes", "--no-watch"])
    assert result.exit_code == 0, result.stdout

    # No project-scope files should exist
    assert not (project / ".mcp.json").exists()
    assert not (project / ".cursor").exists()
    assert not (project / ".qoder").exists()
    assert not (project / "opencode.json").exists()
    assert not (project / "config.toml").exists()
