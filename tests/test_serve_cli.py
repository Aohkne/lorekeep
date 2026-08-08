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

    # Patch MCP construction to a no-op server so CLI does not block on stdio.
    import lorekeep.mcp_server as ms
    ran = {"ok": False, "profile": None}

    class FakeMCP:
        def run(self, transport=None):
            ran["ok"] = True
    def fake_create(profile):
        ran["profile"] = profile
        return FakeMCP()

    monkeypatch.setattr(ms, "create_mcp", fake_create)

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert ran["ok"] is True
    assert ran["profile"] == "core"


def test_serve_rejects_unknown_profile(tmp_path: Path, fixtures: Path, monkeypatch):
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))

    result = runner.invoke(app, ["serve", "--profile", "wide"])

    assert result.exit_code == 1
    assert "choose core|full" in result.stdout
