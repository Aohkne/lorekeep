"""Tests for agent auto-detection (env vars + filesystem markers)."""
from lorekeep.integrations.detect import detect_active_agent, detect_installed_agents, detect_agents


def test_detect_active_opencode(isolated_home, monkeypatch):
    monkeypatch.setenv("OPENCODE", "1")
    assert detect_active_agent() == "opencode"


def test_detect_active_claude(isolated_home, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect_active_agent() == "claude"


def test_detect_active_none(isolated_home):
    assert detect_active_agent() is None


def test_detect_active_falsy_env_ignored(isolated_home, monkeypatch):
    monkeypatch.setenv("OPENCODE", "0")
    assert detect_active_agent() is None


def test_detect_installed_finds_markers(isolated_home):
    (isolated_home / ".claude").mkdir()
    (isolated_home / ".config" / "opencode").mkdir(parents=True)
    found = detect_installed_agents()
    assert "claude" in found
    assert "opencode" in found
    assert "codex" not in found


def test_detect_installed_empty(isolated_home):
    assert detect_installed_agents() == []


def test_detect_agents_active_overrides_installed(isolated_home, monkeypatch):
    """When inside an agent session, return only that agent."""
    monkeypatch.setenv("OPENCODE", "1")
    (isolated_home / ".claude").mkdir()
    (isolated_home / ".config" / "opencode").mkdir(parents=True)
    result = detect_agents()
    assert result == ["opencode"]


def test_detect_agents_no_active_returns_all_installed(isolated_home):
    (isolated_home / ".claude").mkdir()
    (isolated_home / ".cursor").mkdir()
    result = detect_agents()
    assert set(result) == {"claude", "cursor"}
