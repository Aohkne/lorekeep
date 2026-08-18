"""Import knowledge from Grok Build sessions into lorekeep raw/ tree.

Grok Build stores sessions as directories:

    $GROK_HOME/sessions/<url-encoded-cwd>/<session-uuid>/
        chat_history.jsonl   — JSONL, one record per message
        summary.json         — session metadata (id, cwd, num_messages)
        signals.json         — analytics (tokens, tools used)
        events.jsonl         — turn lifecycle events (not needed for import)

Each ``chat_history.jsonl`` line is ``{"type": "...", "content": ...}``:

    type=user       — content is a string or list of ``{type:"text", text:"..."}``
    type=assistant  — content is the response text; ``tool_calls`` is an
                      optional list of ``{id, name, arguments}``
    type=system     — system prompt (skipped)
    type=reasoning  — internal model reasoning (skipped)
    type=tool_result — tool output (folded into the preceding assistant turn)

Conversation turns pair a user message with the assistant response(s) that
follow it, matching the same ``ConversationTurn`` shape the Claude importer
uses.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path

from lorekeep.compile.providers import LLMProvider
from lorekeep.importer.claude import (
    ConversationTurn,
    chunk_turns,
    clean_user_message,
    load_import_manifest,
    save_import_manifest,
    summarize_batch,
)
from lorekeep.importer.session_dump import dump_session_turns


def _grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

_SESSIONS_ROOT = "sessions"


def locate_session(cwd: Path | None = None) -> Path | None:
    """Find the most recent Grok Build session dir for the given cwd.

    Grok URL-encodes the cwd into the directory name under
    ``~/.grok/sessions/``.  Multiple sessions per cwd are sub-directories
    keyed by session UUID.  We pick the newest by ``summary.json`` mtime.
    """
    cwd = str((cwd or Path.cwd()).resolve())
    sessions_root = _grok_home() / _SESSIONS_ROOT
    if not sessions_root.is_dir():
        return None

    encoded_cwd = urllib.parse.quote(cwd, safe="")
    project_dir = sessions_root / encoded_cwd
    if not project_dir.is_dir():
        return None

    candidates = [
        d for d in project_dir.iterdir()
        if d.is_dir() and (d / "chat_history.jsonl").is_file()
    ]
    if not candidates:
        return None

    def _mtime(d: Path) -> float:
        summary = d / "summary.json"
        if summary.is_file():
            try:
                return summary.stat().st_mtime
            except OSError:
                pass
        try:
            return (d / "chat_history.jsonl").stat().st_mtime
        except OSError:
            return 0.0

    return max(candidates, key=_mtime)


def session_key(session_dir: Path) -> str:
    """Stable identifier: ``<cwd-basename>-<short-uuid>``."""
    uuid = session_dir.name  # session UUID
    # Parent dir name is the URL-encoded cwd; decode and take basename.
    encoded_cwd = session_dir.parent.name
    cwd = urllib.parse.unquote(encoded_cwd)
    cwd_basename = Path(cwd).name or "unknown"
    short_uuid = uuid.split("-")[0] if "-" in uuid else uuid[:8]
    return f"{cwd_basename}-{short_uuid}"


def session_from_hook(event: dict) -> Path | None:
    """Resolve Grok's session directory from its SessionEnd payload."""
    from lorekeep.importer.hook_utils import (
        event_cwd,
        event_text,
        validated_event_path,
    )

    cwd = event_cwd(event)
    if event_text(event, "transcript_path") is not None:
        transcript = validated_event_path(
            event, [_grok_home() / _SESSIONS_ROOT],
        )
        if transcript is None:
            return None
        if transcript.name == "chat_history.jsonl":
            return transcript.parent
    return locate_session(cwd)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def _extract_text(content: str | list | None) -> str:
    """Extract text from a Grok message content field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


def parse_transcript(session_dir: Path) -> list[ConversationTurn]:
    """Parse ``chat_history.jsonl`` into ``ConversationTurn`` list.

    Iterates through records, pairing each user message with the assistant
    text and tool calls that follow it.  ``system`` and ``reasoning`` records
    are skipped (internal model output, not knowledge).  ``tool_result``
    records are folded into the preceding assistant turn's context (their
    content is the tool output, already reflected in the assistant's answer).
    """
    chat_file = session_dir / "chat_history.jsonl"
    if not chat_file.is_file():
        return []

    turns: list[ConversationTurn] = []
    current_user = ""
    current_assistant = ""
    current_tools: list[str] = []

    for line in chat_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        rtype = rec.get("type", "")

        if rtype == "user":
            # Flush the previous turn if we have both sides.
            if current_user and (current_assistant or current_tools):
                turns.append(ConversationTurn(
                    user_content=clean_user_message(current_user),
                    assistant_text=current_assistant,
                    tool_calls=sorted(set(current_tools)),
                ))
            # Start a new turn — but skip system-reminder-only user messages.
            text = _extract_text(rec.get("content"))
            if text.strip().startswith("<system-reminder>"):
                current_user = current_user  # keep previous if this is just a reminder
            else:
                current_user = text
            current_assistant = ""
            current_tools = []

        elif rtype == "assistant":
            text = _extract_text(rec.get("content"))
            if text:
                current_assistant = (current_assistant + "\n" + text).strip() if current_assistant else text
            for tc in rec.get("tool_calls", []):
                name = tc.get("name", "")
                if name:
                    current_tools.append(name)

        elif rtype == "tool_result":
            # Tool output is reflected in the assistant's answer; skip.
            pass

        # system, reasoning → skipped

    # Flush the last turn.
    if current_user and (current_assistant or current_tools):
        turns.append(ConversationTurn(
            user_content=clean_user_message(current_user),
            assistant_text=current_assistant,
            tool_calls=sorted(set(current_tools)),
        ))

    return turns


# ---------------------------------------------------------------------------
# Zero-LLM dump (default path)
# ---------------------------------------------------------------------------

def dump_current_session(
    raw_root: Path,
    cwd: Path | None = None,
    *,
    namespace: str = "grok-session",
    dry_run: bool = False,
    **limits,
) -> list[Path]:
    """Dump this project's Grok Build transcript to markdown — no LLM."""
    session_dir = locate_session(cwd)
    if session_dir is None:
        return []
    return dump_session_turns(
        parse_transcript(session_dir), raw_root,
        namespace=namespace, session_key=session_key(session_dir),
        dry_run=dry_run, **limits,
    )


# ---------------------------------------------------------------------------
# LLM summarization (deep mode)
# ---------------------------------------------------------------------------

def import_session_deep(
    raw_root: Path,
    cwd: Path | None,
    provider: LLMProvider,
    *,
    namespace: str = "grok-session",
    max_chars: int = 20_000,
    max_turn_chars: int = 4_000,
    dry_run: bool = False,
) -> list[Path]:
    """Summarize the Grok Build session through the LLM provider."""
    from lorekeep.importer.session_dump import _clip_turns  # type: ignore[private-import]

    session_dir = locate_session(cwd)
    if session_dir is None:
        return []

    turns = parse_transcript(session_dir)
    if not turns:
        return []

    key = session_key(session_dir)
    batches = chunk_turns(_clip_turns(turns, max_turn_chars), max_chars, overlap=0)

    manifest_key = f"{namespace}:{key}"
    manifest = load_import_manifest(raw_root, namespace)

    dest_dir = raw_root / namespace
    written: list[Path] = []
    prev_summary = ""

    for i, batch in enumerate(batches):
        markdown = summarize_batch(
            batch, i, len(batches), namespace, key, provider, prev_summary,
        )
        prev_summary = markdown[:500]
        dest = dest_dir / f"{key}-{i + 1:03d}.md"
        if dry_run:
            written.append(dest)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(markdown, encoding="utf-8")
        written.append(dest)

    if not dry_run:
        import hashlib
        digest = hashlib.sha256(
            "\n".join(t.user_content + t.assistant_text for t in turns).encode()
        ).hexdigest()
        manifest[manifest_key] = digest
        save_import_manifest(raw_root, namespace, manifest)

    return written
