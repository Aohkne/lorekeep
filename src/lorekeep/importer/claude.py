"""Import knowledge from Claude Code sessions into lorekeep raw/ tree.

Two sources:
1. Memory files (memory/*.md) -- YAML-frontmatter markdown; already curated.
2. Transcript JSONL -- conversation history; summarized by LLM into markdown.

Architecture:
  Claude session  --import-->  raw/<ns>/*.md  --compile-->  facts.jsonl
      (import builds raw/)         (existing pipeline)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from lorekeep.compile.providers import LLMProvider

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ConversationTurn:
    """One user message + the assistant's response text."""

    user_content: str           # cleaned user message
    assistant_text: str         # concatenated assistant text blocks only
    tool_calls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def _project_slug(cwd: Path) -> str:
    """Claude Code's project dir name: -<abspath-with-slashes-as-dashes>."""
    slug = str(cwd.absolute()).lstrip("/").replace("/", "-").replace("_", "-")
    return "-" + slug


def _claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def find_current_session(cwd: Path | None = None) -> Path | None:
    """Find the Claude session directory for the current project.

    Returns the directory containing memory/ + the transcript JSONL
    for the most recently modified transcript.
    """
    cwd = cwd or Path.cwd()
    project_dir = _claude_config_dir() / "projects" / _project_slug(cwd)

    if not project_dir.is_dir():
        return None

    # Pick the newest .jsonl transcript (not the compacted subdir)
    transcripts = sorted(
        [p for p in project_dir.iterdir() if p.suffix == ".jsonl"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not transcripts:
        return None

    return project_dir


# ---------------------------------------------------------------------------
# XML tag stripping
# ---------------------------------------------------------------------------

_COMMAND_TAG_RE = re.compile(r"<command-(?:message|name|args)>[^<]*</command-(?:message|name|args)>\n?")


def clean_user_message(raw: str) -> str:
    """Strip Claude's internal XML tags, keeping the real message text."""
    return _COMMAND_TAG_RE.sub("", raw).strip()


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


def parse_transcript(transcript_path: Path) -> list[ConversationTurn]:
    """Parse a Claude session JSONL transcript into structured turns.

    A "turn" = one user message followed by the assistant's text blocks.
    ``thinking`` and ``tool_use`` content blocks are excluded from
    assistant_text but tool_use names are recorded in tool_calls.
    """
    # We need the top-level wrapper entries that carry ``role``.
    entries: list[dict] = []
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue   # skip corrupt lines
        # Accept both real-format (message.role nested) and test-fixture (top-level role)
        msg = d.get("message", {})
        if "role" in d or isinstance(msg, dict) and "role" in msg:
            entries.append(d)

    def _entry_role(d: dict) -> str | None:
        """Resolve role from top-level or nested message.role."""
        if "role" in d:
            return d["role"]
        msg = d.get("message", {})
        if isinstance(msg, dict) and "role" in msg:
            return msg["role"]
        return None

    def _extract_text_content(raw: str | list) -> str:
        """Extract clean text from content — handles both string and list[dict]."""
        if isinstance(raw, str):
            return clean_user_message(raw)
        if isinstance(raw, list):
            parts = []
            for block in raw:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return clean_user_message(" ".join(parts))
        return ""

    turns: list[ConversationTurn] = []
    current_user: str | None = None
    current_assistant: list[str] = []
    current_tools: list[str] = []

    for entry in entries:
        role = _entry_role(entry)
        msg = entry.get("message", {})

        if role == "user":
            # Flush previous turn
            if current_user is not None:
                turns.append(ConversationTurn(
                    user_content=current_user,
                    assistant_text="\n\n".join(current_assistant),
                    tool_calls=list(current_tools),
                ))
            raw_content = msg.get("content", "") if isinstance(msg, dict) else ""
            current_user = _extract_text_content(raw_content)
            current_assistant = []
            current_tools = []

        elif role == "assistant":
            content_blocks = msg.get("content") if isinstance(msg, dict) else []
            if isinstance(content_blocks, list):
                for block in content_blocks:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            current_assistant.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "unknown")
                            if name not in current_tools:
                                current_tools.append(name)

    # Flush final turn
    if current_user is not None:
        turns.append(ConversationTurn(
            user_content=current_user,
            assistant_text="\n\n".join(current_assistant),
            tool_calls=list(current_tools),
        ))

    return turns


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_turns(
    turns: list[ConversationTurn],
    max_chars: int = 20_000,
    overlap: int = 2,
) -> list[list[ConversationTurn]]:
    """Split turns into LLM-context-sized batches with overlap."""
    if not turns:
        return []

    batches: list[list[ConversationTurn]] = []
    i = 0
    while i < len(turns):
        batch: list[ConversationTurn] = []
        chars = 0
        j = i
        while j < len(turns):
            t = turns[j]
            t_len = len(t.user_content) + len(t.assistant_text)
            if batch and chars + t_len > max_chars:
                break
            batch.append(t)
            chars += t_len
            j += 1
        batches.append(batch)
        # advance with overlap
        i = max(i + 1, j - overlap if j < len(turns) else j)
    return batches


# ---------------------------------------------------------------------------
# Zero-LLM session dump
# ---------------------------------------------------------------------------


def locate_session(cwd: Path | None = None) -> Path | None:
    """Newest transcript JSONL for this project, or None."""
    project_dir = find_current_session(cwd)
    if project_dir is None:
        return None
    transcripts = sorted(
        (p for p in project_dir.iterdir() if p.suffix == ".jsonl"),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    return transcripts[0] if transcripts else None


def session_key(transcript_path: Path) -> str:
    return transcript_path.stem


def session_from_hook(event: dict) -> Path | None:
    """Resolve the exact Claude transcript named by a SessionEnd payload."""
    from lorekeep.importer.hook_utils import (
        event_cwd,
        event_text,
        validated_event_path,
    )

    cwd = event_cwd(event)
    if event_text(event, "transcript_path") is not None:
        return validated_event_path(
            event, [_claude_config_dir() / "projects"],
        )
    return locate_session(cwd)


def dump_current_session(
    raw_root: Path,
    cwd: Path | None = None,
    *,
    namespace: str = "claude-session",
    dry_run: bool = False,
    **limits,
) -> list[Path]:
    """Dump this project's Claude transcript to markdown — no LLM involved."""
    from lorekeep.importer.session_dump import dump_session_turns

    transcript = locate_session(cwd)
    if transcript is None:
        return []
    return dump_session_turns(
        parse_transcript(transcript), raw_root,
        namespace=namespace, session_key=session_key(transcript),
        dry_run=dry_run, **limits,
    )


# ---------------------------------------------------------------------------
# LLM summarization (deep mode)
# ---------------------------------------------------------------------------


TRANSCRIPT_SYSTEM_PROMPT = """\
You are a knowledge documenter. Read the conversation transcript between a \
developer and an AI coding agent, and produce a structured markdown document \
capturing: (1) key technical decisions made, (2) architecture/design choices, \
(3) explicit requirements and constraints, (4) tools, libraries, or patterns \
selected, (5) reasons behind each choice, (6) open questions or deferred work.

Output ONLY well-structured markdown. No JSON wrapping, no preamble."""


def _batch_prompt(
    turns: list[ConversationTurn],
    batch_index: int,
    total_batches: int,
    namespace: str,
    session_id: str,
    previous_summary: str = "",
) -> str:
    conversation = []
    for t in turns:
        conversation.append(f"## Developer\n{t.user_content}\n")
        if t.assistant_text:
            conversation.append(f"## AI Agent\n{t.assistant_text}\n")
    text = "\n".join(conversation)

    prev = f"Previous batch summary:\n{previous_summary}\n\n" if previous_summary else ""
    return (
        f"{prev}"
        f"Context: batch {batch_index + 1}/{total_batches}, "
        f"namespace={namespace}, session={session_id}\n\n"
        f"Conversation transcript:\n\n{text}\n\n"
        f"Produce a structured knowledge markdown document for this batch. "
        f"Sections: ## Decisions, ## Architecture, ## Requirements, "
        f"## Tools & Patterns, ## Open Questions"
    )


def summarize_batch(
    turns: list[ConversationTurn],
    batch_index: int,
    total_batches: int,
    namespace: str,
    session_id: str,
    provider: LLMProvider,
    previous_summary: str = "",
) -> str:
    """Call LLM to summarize a batch of conversation turns into markdown."""
    return provider.complete(
        TRANSCRIPT_SYSTEM_PROMPT,
        _batch_prompt(turns, batch_index, total_batches, namespace,
                      session_id, previous_summary),
    )


# ---------------------------------------------------------------------------
# Memory import (quick mode)
# ---------------------------------------------------------------------------


def import_memories(
    session_dir: Path,
    raw_root: Path,
    namespace: str = "claude-memory",
    *,
    dry_run: bool = False,
) -> list[Path]:
    """Copy memory/*.md files from the session into raw/<namespace>/.

    Returns list of written paths.  Idempotent: skips if a file with the
    same SHA-256 content hash already exists in the destination.
    """
    return _copy_memory_files(session_dir / "memory", raw_root, namespace, dry_run=dry_run)


def _copy_memory_files(
    memory_dir: Path,
    raw_root: Path,
    namespace: str,
    *,
    dry_run: bool = False,
) -> list[Path]:
    if not memory_dir.is_dir():
        return []

    dest_dir = raw_root / namespace
    manifest = load_import_manifest(raw_root, namespace)
    written: list[Path] = []

    for src in sorted(memory_dir.glob("*.md")):
        content = src.read_text(encoding="utf-8")
        h = hashlib.sha256(content.encode()).hexdigest()
        if manifest.get(str(src)) == h:
            continue             # already imported, unchanged

        if dry_run:
            written.append(dest_dir / src.name)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        dest.write_text(content, encoding="utf-8")
        manifest[str(src)] = h
        written.append(dest)

    if not dry_run:
        save_import_manifest(raw_root, namespace, manifest)

    return written


def memories_dir() -> Path | None:
    """Locate the current session's memory directory, if any."""
    session_dir = find_current_session()
    if session_dir is None:
        return None
    memory_dir = session_dir / "memory"
    return memory_dir if memory_dir.is_dir() else None


def quick_import(
    raw_root: Path,
    *,
    namespace: str = "claude-memory",
    memory_dir: Path | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Registry-uniform memory import.

    Pass ``memory_dir`` when the caller already located it (the daemon does)
    to skip a redundant session lookup.
    """
    if memory_dir is None:
        memory_dir = memories_dir()
    if memory_dir is None:
        return []
    return _copy_memory_files(memory_dir, raw_root, namespace, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Deep session import
# ---------------------------------------------------------------------------


def import_session_deep(
    session_dir: Path,
    raw_root: Path,
    namespace: str = "claude-session",
    provider: LLMProvider | None = None,
    *,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> list[Path]:
    """Deep-import the session transcript: parse, chunk, summarize each batch
    via an LLM, and write knowledge markdown to raw/<namespace>/.

    Returns list of written file paths.  Requires a provider.
    """
    transcripts = sorted(
        [p for p in session_dir.iterdir() if p.suffix == ".jsonl"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not transcripts:
        return []

    jsonl = transcripts[0]
    session_id = session_dir.name

    # Dedup: skip if transcript content unchanged since last import
    transcript_hash = hashlib.sha256(
        jsonl.read_bytes()
    ).hexdigest()
    manifest = load_import_manifest(raw_root, namespace)
    manifest_key = str(jsonl)

    if not dry_run and manifest.get(manifest_key) == transcript_hash:
        return []

    turns = parse_transcript(jsonl)
    if not turns:
        return []

    batches = chunk_turns(turns, max_chars=batch_max_chars)
    dest_dir = raw_root / namespace
    written: list[Path] = []

    previous_summary = ""
    for i, batch in enumerate(batches):
        if dry_run:
            dest = dest_dir / f"session-{session_id}-batch-{i + 1:02d}.md"
            written.append(dest)
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"session-{session_id}-batch-{i + 1:02d}.md"

        try:
            if provider is None:
                raise RuntimeError("deep mode requires a provider")
            md = summarize_batch(
                batch, i, len(batches), namespace, session_id,
                provider, previous_summary,
            )
        except Exception as exc:
            md = f"# Error summarizing batch {i + 1}\n\nLLM call failed: {exc}\n"

        dest.write_text(md, encoding="utf-8")
        previous_summary = md[:1000]   # carry short context forward
        written.append(dest)

    if not dry_run and written:
        manifest[manifest_key] = transcript_hash
        save_import_manifest(raw_root, namespace, manifest)

    return written


# ---------------------------------------------------------------------------
# Import manifest (idempotent re-import)
# ---------------------------------------------------------------------------


def _manifest_path(raw_root: Path, namespace: str) -> Path:
    return raw_root / namespace / ".import-manifest.json"


def load_import_manifest(raw_root: Path, namespace: str) -> dict[str, str]:
    """Return {source_path: content_hash} dict."""
    p = _manifest_path(raw_root, namespace)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_import_manifest(raw_root: Path, namespace: str, manifest: dict[str, str]) -> None:
    p = _manifest_path(raw_root, namespace)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def import_claude(
    raw_root: Path,
    session_dir: Path,
    *,
    quick: bool = False,
    memory_ns: str = "claude-memory",
    session_ns: str = "claude-session",
    provider: LLMProvider | None = None,
    batch_max_chars: int = 20_000,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Import a Claude session into the lorekeep raw/ tree.

    Returns ``{"memory": [...], "session": [...]}`` with written file paths.
    In *quick* mode, only memory files are imported.
    """
    result: dict[str, list[Path]] = {"memory": [], "session": []}

    # 1. Memory files (always imported)
    result["memory"] = import_memories(
        session_dir, raw_root, namespace=memory_ns, dry_run=dry_run,
    )

    # 2. Session transcript (deep mode only)
    if not quick:
        result["session"] = import_session_deep(
            session_dir, raw_root, namespace=session_ns,
            provider=provider, batch_max_chars=batch_max_chars,
            dry_run=dry_run,
        )

    return result
