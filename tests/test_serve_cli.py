from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_serve_invokes_mcp_run(tmp_path: Path, fixtures: Path, monkeypatch):
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LAPUTA_OUT", str(out))
    monkeypatch.setenv("LAPUTA_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LAPUTA_NS", "teams/backend")

    # Patch mcp.run to a no-op so the CLI returns without blocking on stdio.
    import laputa.mcp_server as ms
    ran = {"ok": False}

    class FakeMCP:
        def run(self, transport=None):
            ran["ok"] = True
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert ran["ok"] is True
