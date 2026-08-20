"""Small helpers shared by targeted transcript importers."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def event_text(event: dict[str, Any], key: str) -> str | None:
    value = event.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def validated_event_path(
    event: dict[str, Any],
    roots: Iterable[Path],
    *,
    expect: str = "file",
) -> Path | None:
    """Resolve ``transcript_path`` only beneath an agent-owned data root."""
    raw = event_text(event, "transcript_path")
    if raw is None:
        return None
    candidate = Path(raw).expanduser().resolve()
    allowed = False
    for root in roots:
        try:
            candidate.relative_to(Path(root).expanduser().resolve())
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        return None
    if expect == "file" and not candidate.is_file():
        return None
    if expect == "dir" and not candidate.is_dir():
        return None
    return candidate


def event_cwd(event: dict[str, Any]) -> Path | None:
    raw = event_text(event, "cwd")
    return Path(raw) if raw else None
