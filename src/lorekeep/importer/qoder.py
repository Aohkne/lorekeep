"""Zero-LLM import for Qoder CLI/IDE transcript JSONL files."""
from __future__ import annotations

import json
import os
from pathlib import Path

from lorekeep.importer._jsonl_turns import parse_role_jsonl
from lorekeep.importer.hook_utils import event_cwd, validated_event_path


def _qoder_home() -> Path:
    return Path(os.environ.get("QODER_CONFIG_DIR", Path.home() / ".qoder"))


def _transcript_root() -> Path:
    return _qoder_home() / "projects"


def _transcript_cwd(path: Path) -> Path | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:64]
    except OSError:
        return None
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        raw = record.get("cwd")
        if not raw and isinstance(record.get("data"), dict):
            raw = record["data"].get("cwd")
        if isinstance(raw, str) and raw:
            return Path(raw)
    return None


def locate_session(cwd: Path | None = None) -> Path | None:
    root = _transcript_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        root.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if cwd is None:
        return candidates[0] if candidates else None
    wanted = cwd.resolve()
    for path in candidates:
        recorded = _transcript_cwd(path)
        if recorded is not None and recorded.resolve() == wanted:
            return path
    return candidates[0] if candidates else None


def session_from_hook(event: dict) -> Path | None:
    from lorekeep.importer.hook_utils import event_text

    cwd = event_cwd(event)
    if event_text(event, "transcript_path") is not None:
        return validated_event_path(event, [_transcript_root()])
    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id:
        if not _transcript_root().is_dir():
            return None
        matches = [
            path for path in _transcript_root().rglob("*.jsonl")
            if path.stem == session_id
        ]
        if matches:
            return sorted(matches)[0]
        return None
    return locate_session(cwd)


def parse_transcript(path: Path):
    return parse_role_jsonl(path)


def session_key(path: Path) -> str:
    return path.stem


def dump_current_session(
    raw_root: Path,
    cwd: Path | None = None,
    *,
    namespace: str = "qoder-session",
    dry_run: bool = False,
    **limits,
) -> list[Path]:
    from lorekeep.importer.session_dump import dump_session_turns

    transcript = locate_session(cwd)
    if transcript is None:
        return []
    return dump_session_turns(
        parse_transcript(transcript), raw_root,
        namespace=namespace, session_key=session_key(transcript),
        dry_run=dry_run, **limits,
    )
