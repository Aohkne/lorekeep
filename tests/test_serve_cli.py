from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_serve_invokes_mcp_run(tmp_path: Path, fixtures: Path, monkeypatch):
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_NS", "teams/backend")

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


def test_serve_auto_expands_namespaces(tmp_path: Path, fixtures: Path, monkeypatch):
    """Local serve sees every namespace in the graph, not just LOREKEEP_NS."""
    import shutil, json
    out = tmp_path / "graph"
    out.mkdir()
    # Build a facts.jsonl with nodes in two namespaces
    facts = [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["me"], "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["claude-session"], "props": {"name": "b"}},
    ]
    (out / "facts.jsonl").write_text("\n".join(json.dumps(f) for f in facts))
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_NS", "me")

    import lorekeep.mcp_server as ms

    # Capture what configure receives, then build a real ScopedGraph so we
    # can verify the expansion made claude-session visible.
    captured = {}

    real_configure = ms.configure

    def spy_configure(**kwargs):
        captured["allowed_ns"] = list(kwargs.get("allowed_ns", []))
        real_configure(**kwargs)

    monkeypatch.setattr(ms, "configure", spy_configure)

    class FakeMCP:
        def run(self, transport=None):
            pass
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    # me was in LOREKEEP_NS, but claude-session from the graph must be auto-included
    assert "me" in captured["allowed_ns"]
    assert "claude-session" in captured["allowed_ns"]
    # The scoped graph should see both nodes
    assert ms.get_node("svc:a") is not None
    assert ms.get_node("svc:b") is not None
