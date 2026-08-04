"""Privacy-safe diagnostics for GitHub issue reports."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lorekeep import __version__
from lorekeep.paths import resolve_paths
from lorekeep.redaction import redact_text

LOG_TAIL_LINES = 1000
REPORT_EVENT_LINES = 20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _display_path(path: Path, home: Path) -> str:
    try:
        rel = path.resolve().relative_to(home.resolve())
        return "~" if not rel.parts else f"~/{rel.as_posix()}"
    except (OSError, ValueError):
        return redact_text(path)


def _file_health(path: Path) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable ({type(exc).__name__})"
    kind = "dir" if path.is_dir() else "file"
    return f"{kind}, {stat.st_size} bytes"


def _safe_config(config_path: Path) -> dict[str, Any]:
    """Load only explicitly allowlisted, non-content config fields."""
    try:
        from lorekeep.config import load_config
        cfg = load_config(config_path)
    except Exception as exc:
        return {"status": f"invalid ({type(exc).__name__})"}
    return {
        "status": "loaded" if config_path.exists() else "defaults",
        "provider_model": cfg.provider.model,
        "api_key_env_name": cfg.provider.api_key_env or "not configured",
        "compile_chunk_lines": cfg.compile.chunk_lines,
        "observability_provider": cfg.observability.provider or "disabled",
        "install_source": cfg.install_source or "unknown",
    }


def _manifest_summary(path: Path) -> dict[str, Any]:
    try:
        from lorekeep.models import Manifest
        manifest = Manifest.from_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "missing"}
    except Exception as exc:
        return {"status": f"invalid ({type(exc).__name__})"}
    return {
        "status": "loaded",
        "compiled_at": manifest.compiled_at or "unknown",
        "run_id": manifest.run_id,
        "facts_hash_prefix": manifest.facts_hash[:12],
        "chunk_count": manifest.chunk_count,
        "node_count": manifest.node_count,
        "edge_count": manifest.edge_count,
        "error_count": len(manifest.errors),
        "quarantined_count": manifest.quarantined_count,
        "flagged_count": manifest.flagged_count,
        "merged_count": manifest.merged_count,
    }


def _service_status() -> str:
    try:
        from lorekeep.daemon_service import status
        return redact_text(status())
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def _log_tail(log_path: Path, lines: int = LOG_TAIL_LINES) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return "runtime log is missing\n"
    except OSError as exc:
        return f"runtime log is unreadable ({type(exc).__name__})\n"
    return "\n".join(redact_text(line) for line in content[-lines:]) + "\n"


def _recent_events(log_path: Path, limit: int = REPORT_EVENT_LINES) -> list[str]:
    """Return structured warning/error headers, excluding traceback bodies."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    levels = (" level=WARNING ", " level=ERROR ", " level=CRITICAL ")
    return [redact_text(line) for line in lines if any(level in line for level in levels)][-limit:]


def build_report() -> str:
    """Build copy/paste-ready Markdown without raw knowledge content."""
    paths = resolve_paths()
    home = paths["home"]
    safe_config = _safe_config(paths["config"])
    manifest = _manifest_summary(paths["out"] / "manifest.json")
    generated = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = [
        ("Lorekeep", __version__),
        ("Python", platform.python_version()),
        ("Platform", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("Generated", generated),
        ("Daemon", _service_status()),
    ]
    config_rows = [(key, value) for key, value in safe_config.items()]
    manifest_rows = [(key, value) for key, value in manifest.items()]
    path_rows = [
        (key, f"{_display_path(paths[key], home)} — {_file_health(paths[key])}")
        for key in ("home", "config", "schema", "raw", "out", "pending", "wiki", "logs")
    ]
    legacy_rows = [
        (name, _file_health(home / name))
        for name in ("agent.log", "daemon.log", "daemon.err.log")
    ]
    recent_events = _recent_events(paths["logs"] / "lorekeep.log")
    recent_block = "\n".join(recent_events) if recent_events else "No warning/error events found."

    def table(items: list[tuple[str, Any]]) -> str:
        return "\n".join(
            f"| {redact_text(k)} | {redact_text(v, home=home).replace('|', '\\|')} |"
            for k, v in items
        )

    return (
        "# Lorekeep support report\n\n"
        "> Metadata only. Raw documents, graph facts, prompts, queries, journal "
        "content and credential values are excluded.\n\n"
        "## Runtime\n\n| Field | Value |\n|---|---|\n" + table(rows) + "\n\n"
        "## Safe configuration\n\n| Field | Value |\n|---|---|\n" + table(config_rows) + "\n\n"
        "## Graph manifest summary\n\n| Field | Value |\n|---|---|\n" + table(manifest_rows) + "\n\n"
        "## Path health\n\n| Path | State |\n|---|---|\n" + table(path_rows) + "\n\n"
        "## Legacy logs\n\n| File | State |\n|---|---|\n" + table(legacy_rows) + "\n"
        "\n## Recent warning/error events\n\n```text\n" + recent_block + "\n```\n"
    )


def write_report(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(), encoding="utf-8")
    _private_file(output)
    return output


def _private_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def create_bundle(output: Path | None = None) -> tuple[Path, str]:
    """Create an allowlisted support archive and return (path, sha256)."""
    paths = resolve_paths()
    if output is None:
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        output = Path.cwd() / f"lorekeep-support-{stamp}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_report()
    tail = _log_tail(paths["logs"] / "lorekeep.log")
    summary = json.dumps(
        _manifest_summary(paths["out"] / "manifest.json"),
        indent=2, sort_keys=True, ensure_ascii=False,
    ) + "\n"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", report)
        archive.writestr("logs/runtime-tail.log", tail)
        archive.writestr("manifest-summary.json", summary)
    _private_file(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return output, digest
