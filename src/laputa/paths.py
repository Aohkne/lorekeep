"""Path resolution with 4-tier precedence (high -> low).

1. explicit per-path env (LAPUTA_RAW/OUT/CACHE/SCHEMA/CONFIG) - tests + power users
2. LAPUTA_HOME -> unified <home>/{config.yaml,schema.json,raw,graph,cache.json}
3. dev mode (.laputa/ or raw/ in CWD, or LAPUTA_DEV=1) -> current CWD layout
4. default -> XDG (platformdirs): config + data dirs

Pure: no I/O, no side effects. Fully testable.
"""
from __future__ import annotations

import os
from pathlib import Path


def _dev_marker(cwd: Path) -> bool:
    return (cwd / ".laputa").is_dir() or (cwd / "raw").is_dir()


def resolve_paths() -> dict[str, Path]:
    cwd = Path.cwd()
    home = os.environ.get("LAPUTA_HOME")
    dev = os.environ.get("LAPUTA_DEV") == "1" or _dev_marker(cwd)

    if home:
        base = Path(home).expanduser()
        config = base / "config.yaml"
        cache = base / "cache.json"
        raw = base / "raw"
        out = base / "graph"
        schema = base / "schema.json"
    elif dev:
        config = cwd / ".laputa" / "config.yaml"
        cache = cwd / ".laputa" / "cache.json"
        raw = cwd / "raw"
        out = cwd / "graph"
        schema = cwd / "graph" / "schema.json"
    else:
        from platformdirs import user_config_dir, user_data_dir
        cfg_dir = Path(user_config_dir("laputa"))
        data_dir = Path(user_data_dir("laputa"))
        config = cfg_dir / "config.yaml"
        cache = data_dir / "cache.json"
        raw = data_dir / "raw"
        out = data_dir / "graph"
        schema = data_dir / "schema.json"

    def override(env_name: str, current: Path) -> Path:
        v = os.environ.get(env_name)
        return Path(v).expanduser() if v else current

    return {
        "raw": override("LAPUTA_RAW", raw),
        "out": override("LAPUTA_OUT", out),
        "cache": override("LAPUTA_CACHE", cache),
        "schema": override("LAPUTA_SCHEMA", schema),
        "config": override("LAPUTA_CONFIG", config),
    }
