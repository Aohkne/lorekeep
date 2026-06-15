from pathlib import Path
from laputa.paths import resolve_paths


def test_dev_mode_via_laputa_marker(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".laputa").mkdir()
    p = resolve_paths()
    assert p["config"] == tmp_path / ".laputa" / "config.yaml"
    assert p["cache"] == tmp_path / ".laputa" / "cache.json"
    assert p["raw"] == tmp_path / "raw"
    assert p["out"] == tmp_path / "graph"
    assert p["schema"] == tmp_path / "graph" / "schema.json"


def test_dev_mode_via_raw_marker(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "raw").mkdir()
    assert resolve_paths()["raw"] == tmp_path / "raw"


def test_laputa_home_overrides_dev(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".laputa").mkdir()
    home = tmp_path / "myhome"
    monkeypatch.setenv("LAPUTA_HOME", str(home))
    p = resolve_paths()
    assert p["config"] == home / "config.yaml"
    assert p["raw"] == home / "raw"
    assert p["schema"] == home / "schema.json"


def test_xdg_default(tmp_path: Path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("LAPUTA_HOME", raising=False)
    monkeypatch.delenv("LAPUTA_DEV", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    p = resolve_paths()
    assert p["config"] == tmp_path / "xdg-config" / "laputa" / "config.yaml"
    assert p["raw"] == tmp_path / "xdg-data" / "laputa" / "raw"
    assert p["schema"] == tmp_path / "xdg-data" / "laputa" / "schema.json"


def test_explicit_env_overrides_everything(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".laputa").mkdir()                 # dev mode active
    monkeypatch.setenv("LAPUTA_RAW", "/custom/raw")
    monkeypatch.setenv("LAPUTA_OUT", "/custom/graph")
    monkeypatch.setenv("LAPUTA_CONFIG", "/custom/config.yaml")
    p = resolve_paths()
    assert p["raw"] == Path("/custom/raw")
    assert p["out"] == Path("/custom/graph")
    assert p["config"] == Path("/custom/config.yaml")
    assert p["schema"] == tmp_path / "graph" / "schema.json"
