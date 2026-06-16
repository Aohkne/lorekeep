import shutil
from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_doctor_ok(tmp_path: Path, fixtures: Path, monkeypatch):
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LOREKEEP_NS", "teams/backend")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "all checks passed" in result.stdout.lower()


def test_doctor_missing_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOREKEEP_OUT", str(tmp_path / "nope"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "facts.jsonl not found" in result.stdout.lower()
