"""Zero-LLM import for Command Code transcript JSONL files."""
from __future__ import annotations

import os
from pathlib import Path

from lorekeep.importer._jsonl_turns import parse_role_jsonl
from lorekeep.importer.hook_utils import event_cwd, event_text, validated_event_path


def _commandcode_home() -> Path:
    return Path(os.environ.get(
        "COMMANDCODE_HOME", Path.home() / ".commandcode"
    ))


def _transcript_root() -> Path:
    return _commandcode_home() / "projects"


# Command Code stores checkpoints and prompt history next to each transcript
# as <id>.checkpoints.jsonl / <id>.prompts.jsonl. They are sidecars, not
# conversations — and they can be newer than the transcript itself.
_SIDECAR_SUFFIXES = (".checkpoints", ".prompts", ".share")


def _is_sidecar(path: Path) -> bool:
    return any(
        path.name.endswith(suffix + ".jsonl") for suffix in _SIDECAR_SUFFIXES
    )


def locate_session(cwd: Path | None = None) -> Path | None:
    root = _transcript_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        (path for path in root.rglob("*.jsonl") if not _is_sidecar(path)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def session_from_hook(event: dict) -> Path | None:
    cwd = event_cwd(event)
    if event_text(event, "transcript_path") is not None:
        return validated_event_path(event, [_commandcode_home()])
    return locate_session(cwd)


def parse_transcript(path: Path):
    return parse_role_jsonl(path)


def session_key(path: Path) -> str:
    return path.stem


def dump_current_session(
    raw_root: Path,
    cwd: Path | None = None,
    *,
    namespace: str = "cmd-session",
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
