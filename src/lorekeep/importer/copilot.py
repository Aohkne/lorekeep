"""Zero-LLM import for GitHub Copilot CLI local session events."""
from __future__ import annotations

import json
import os
from pathlib import Path

from lorekeep.importer.claude import ConversationTurn


def _copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))


def _session_root() -> Path:
    return _copilot_home() / "session-state"


def locate_session(cwd: Path | None = None) -> Path | None:
    root = _session_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        root.glob("*/events.jsonl"), key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if cwd is None:
        return candidates[0] if candidates else None
    wanted = str(cwd.resolve())
    for path in candidates:
        try:
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, IndexError, json.JSONDecodeError):
            continue
        if not isinstance(first, dict):
            continue
        data = first.get("data")
        context = data.get("context") if isinstance(data, dict) else None
        if isinstance(context, dict) and context.get("cwd") == wanted:
            return path
    return candidates[0] if candidates else None


def session_from_hook(event: dict) -> Path | None:
    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id:
        candidate = (_session_root() / session_id / "events.jsonl").resolve()
        try:
            candidate.relative_to(_session_root().resolve())
        except ValueError:
            return None
        if candidate.is_file():
            return candidate
        return None
    cwd = event.get("cwd")
    return locate_session(Path(cwd) if isinstance(cwd, str) and cwd else None)


def _content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item.get("text", "") for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    if isinstance(value, dict):
        for key in ("content", "text", "message"):
            text = value.get(key)
            if isinstance(text, str):
                return text
    return ""


def parse_transcript(path: Path) -> list[ConversationTurn]:
    turns: list[ConversationTurn] = []
    user: str | None = None
    assistant: list[str] = []
    tools: list[str] = []

    def flush() -> None:
        nonlocal user, assistant, tools
        if user is not None:
            turns.append(ConversationTurn(
                user_content=user,
                assistant_text="\n\n".join(assistant),
                tool_calls=list(tools),
            ))
        user, assistant, tools = None, [], []

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        if kind == "user.message":
            flush()
            user = _content(data.get("content"))
        elif kind == "assistant.message":
            text = _content(data.get("content") or data.get("message"))
            if text:
                assistant.append(text)
            requests = data.get("toolRequests") or []
            if isinstance(requests, list):
                for request in requests:
                    if not isinstance(request, dict):
                        continue
                    name = request.get("name") or request.get("toolName")
                    if isinstance(name, str) and name not in tools:
                        tools.append(name)
        elif kind == "tool.execution_start":
            name = data.get("toolName") or data.get("name")
            if isinstance(name, str) and name not in tools:
                tools.append(name)
    flush()
    return turns


def session_key(path: Path) -> str:
    return path.parent.name


def dump_current_session(
    raw_root: Path,
    cwd: Path | None = None,
    *,
    namespace: str = "copilot-session",
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
