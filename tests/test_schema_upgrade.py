import json

from lorekeep.defaults import DEFAULT_SCHEMA, DEFAULT_SCHEMA_V2
from lorekeep.schema_io import upgrade_schema
from typer.testing import CliRunner

from lorekeep.cli import app


def test_upgrade_stock_v2_creates_backup_and_is_idempotent(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(DEFAULT_SCHEMA_V2))

    result = upgrade_schema(path)

    assert result["changed"] is True
    assert json.loads(path.read_text()) == DEFAULT_SCHEMA
    backup = tmp_path / "schema.v2.backup.json"
    assert json.loads(backup.read_text()) == DEFAULT_SCHEMA_V2
    assert upgrade_schema(path)["changed"] is False


def test_upgrade_dry_run_does_not_write(tmp_path):
    path = tmp_path / "schema.json"
    original = json.dumps(DEFAULT_SCHEMA_V2)
    path.write_text(original)

    result = upgrade_schema(path, dry_run=True)

    assert result["changed"] is True
    assert path.read_text() == original
    assert not (tmp_path / "schema.v2.backup.json").exists()


def test_upgrade_refuses_custom_schema_without_force(tmp_path):
    path = tmp_path / "schema.json"
    custom = {**DEFAULT_SCHEMA_V2, "node_types": {
        **DEFAULT_SCHEMA_V2["node_types"],
        "custom": {"props": {"name": "string"}},
    }}
    path.write_text(json.dumps(custom))

    result = upgrade_schema(path)

    assert result["custom"] is True
    assert result["changed"] is False
    assert json.loads(path.read_text()) == custom


def test_schema_upgrade_cli_upgrades_existing_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "schema.json").write_text(json.dumps(DEFAULT_SCHEMA_V2))
    monkeypatch.setenv("LOREKEEP_HOME", str(home))

    result = CliRunner().invoke(app, ["schema", "upgrade"])

    assert result.exit_code == 0, result.output
    assert "v2" in result.output and "v3" in result.output
    assert json.loads((home / "schema.json").read_text()) == DEFAULT_SCHEMA
