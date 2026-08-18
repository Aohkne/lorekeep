import json
from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_eval_construction_command(tmp_path: Path, fixtures: Path, monkeypatch):
    # build a compiled graph equal to gold so scores are perfect
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text(
        (fixtures / "gold/payments.facts.jsonl").read_text())
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_GOLD", str(fixtures / "gold"))
    monkeypatch.setenv("LOREKEEP_EVAL_RESULTS", str(tmp_path / "results.json"))

    result = runner.invoke(app, ["eval"])
    assert result.exit_code == 0, result.stdout
    assert "f1" in result.stdout
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["extraction"]["nodes"]["f1"] == 1.0


def test_doctor_reports_clean_graph(
    tmp_path: Path, fixtures: Path, monkeypatch, patch_make_provider,
):
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text(
        (fixtures / "gold/payments.facts.jsonl").read_text())
    monkeypatch.setenv("LOREKEEP_OUT", str(out))
    monkeypatch.setenv("LOREKEEP_SCHEMA", str(fixtures / "schema.json"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "ok" in result.stdout.lower() or "passed" in result.stdout.lower()
