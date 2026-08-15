from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app
from lorekeep.config import Config

runner = CliRunner()


def test_runtime_namespaces_accepts_legacy_read_env(monkeypatch):
    from lorekeep.cli import _runtime_namespaces

    monkeypatch.delenv("LOREKEEP_READ_NS", raising=False)
    monkeypatch.setenv("LOREKEEP_NS", "legacy,scope")

    assert _runtime_namespaces(Config()) == (["legacy", "scope"], "me")


def test_runtime_namespaces_new_read_env_wins_over_legacy(monkeypatch):
    from lorekeep.cli import _runtime_namespaces

    monkeypatch.setenv("LOREKEEP_READ_NS", "current")
    monkeypatch.setenv("LOREKEEP_NS", "legacy")

    assert _runtime_namespaces(Config()) == (["current"], "me")


def test_serve_invokes_mcp_run(tmp_path: Path, fixtures: Path, monkeypatch):
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("LOREKEEP_READ_NS", "teams/backend")

    # Patch MCP server to a no-op so CLI does not block on stdio.
    import lorekeep.mcp_server as ms
    ran = {"ok": False}

    class FakeMCP:
        def run(self, transport=None):
            ran["ok"] = True
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert ran["ok"] is True
    assert ms._state["allowed_ns"] == ["teams/backend"]
    assert ms._state["write_ns"] == "me"


def test_serve_defaults_to_all_reads_and_concrete_personal_writes(
    tmp_path: Path, fixtures: Path, monkeypatch,
):
    import shutil

    home = tmp_path / "home"
    out = home / "graph"
    out.mkdir(parents=True)
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    shutil.copy(fixtures / "schema.json", home / "schema.json")
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    monkeypatch.delenv("LOREKEEP_READ_NS", raising=False)
    monkeypatch.delenv("LOREKEEP_NS", raising=False)

    import lorekeep.mcp_server as ms

    class FakeMCP:
        def run(self, transport=None):
            pass

    monkeypatch.setattr(ms, "mcp", FakeMCP())
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.stdout
    assert ms._state["allowed_ns"] == ["*"]
    assert ms._state["write_ns"] == "me"
    note = ms.review_note("improvement", "Record a concrete owner")
    assert note["ns"] == "me"
    assert (home / "pending" / "me" / "journal.jsonl").is_file()


def test_serve_wildcard_expands_session_namespaces(tmp_path: Path, fixtures: Path, monkeypatch):
    """LOREKEEP_READ_NS=me,*-session expands to session namespaces."""
    import json
    out = tmp_path / "graph"
    out.mkdir()
    facts = [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["me"], "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["claude-session"], "props": {"name": "b"}},
    ]
    (out / "facts.jsonl").write_text("\n".join(json.dumps(f) for f in facts))
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_READ_NS", "me,*-session")

    import lorekeep.mcp_server as ms

    class FakeMCP:
        def run(self, transport=None):
            pass
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    # Pattern *-session expanded → claude-session visible
    assert ms.get_node("svc:a") is not None
    assert ms.get_node("svc:b") is not None


def test_serve_literal_only_hides_session_namespaces(tmp_path: Path, fixtures: Path, monkeypatch):
    """LOREKEEP_READ_NS=me hides session-derived facts."""
    import json
    out = tmp_path / "graph"
    out.mkdir()
    facts = [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["me"], "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["claude-session"], "props": {"name": "b"}},
    ]
    (out / "facts.jsonl").write_text("\n".join(json.dumps(f) for f in facts))
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_READ_NS", "me")

    import lorekeep.mcp_server as ms

    class FakeMCP:
        def run(self, transport=None):
            pass
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert ms.get_node("svc:a") is not None
    assert "error" in ms.get_node("svc:b")  # hidden — no wildcard for session ns


def test_serve_star_sees_everything(tmp_path: Path, fixtures: Path, monkeypatch):
    """LOREKEEP_READ_NS=* sees all graph namespaces."""
    import json
    out = tmp_path / "graph"
    out.mkdir()
    facts = [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["me"], "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["claude-session"], "props": {"name": "b"}},
        {"kind": "node", "id": "svc:c", "type": "service", "ns": ["codex-memory"], "props": {"name": "c"}},
    ]
    (out / "facts.jsonl").write_text("\n".join(json.dumps(f) for f in facts))
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_READ_NS", "*")

    import lorekeep.mcp_server as ms

    class FakeMCP:
        def run(self, transport=None):
            pass
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert ms.get_node("svc:a") is not None
    assert ms.get_node("svc:b") is not None
    assert ms.get_node("svc:c") is not None
