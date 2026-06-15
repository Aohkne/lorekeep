import json
from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_compile_command_uses_config_provider(monkeypatch, tmp_path: Path, fixtures: Path):
    # point the CLI at temp dirs via env
    monkeypatch.setenv("LAPUTA_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LAPUTA_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LAPUTA_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LAPUTA_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LAPUTA_PROVIDER", "fake")

    raw = tmp_path / "raw/backend/payments.md"
    raw.parent.mkdir(parents=True)
    raw.write_text((fixtures / "raw/backend/payments.md").read_text())

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "graph/facts.jsonl").exists()
