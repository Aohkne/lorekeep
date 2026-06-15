from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_init_creates_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LAPUTA_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "config.yaml").exists()
    assert (home / "schema.json").exists()
    assert (home / "raw").is_dir()
    assert (home / "graph").is_dir()
    import json
    schema = json.loads((home / "schema.json").read_text())
    assert schema["version"] == 2


def test_init_preserves_existing_config(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    monkeypatch.setenv("LAPUTA_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "config.yaml").read_text() == "install_source: local\n"
    assert (home / "schema.json").exists()
    assert (home / "raw").is_dir()
