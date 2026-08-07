"""Zero-LLM session transcript dump.

Parsed conversation turns become plain markdown under ``raw/<namespace>/``, so
the ordinary compile pipeline extracts facts from them. No provider, no cost —
this is what lets every agent contribute knowledge, not just the two that write
their own memory files.

Determinism contract: this module imports no clock and takes no time argument.
Rendered bytes are a pure function of the turns, so ``extract``'s per-chunk
SHA-256 cache keeps recompiles free and byte-identical.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from lorekeep.importer.claude import (
    ConversationTurn,
    chunk_turns,
    clean_user_message,
    load_import_manifest,
    save_import_manifest,
)

TRUNCATION_MARKER = "\n…[truncated]\n"

_UNSAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_key(session_key: str) -> str:
    """Make a session identifier usable as a filename stem."""
    cleaned = _UNSAFE_KEY_RE.sub("-", session_key).strip("-")
    return cleaned or "unknown"


def turns_digest(turns: Sequence[ConversationTurn]) -> str:
    """Content hash of a conversation, for skipping unchanged re-dumps.

    Agents expose the whole conversation, not a delta, so the daemon re-reads a
    growing prefix every cycle. Hashing it is how we avoid re-rendering it.
    """
    h = hashlib.sha256()
    for t in turns:
        h.update(t.user_content.encode())
        h.update(b"\x00")
        h.update(t.assistant_text.encode())
        h.update(b"\x00")
        for call in t.tool_calls:
            h.update(call.encode())
            h.update(b"\x00")
        h.update(b"\x01")
    return h.hexdigest()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_MARKER


def _clip_turns(
    turns: Sequence[ConversationTurn], max_turn_chars: int
) -> list[ConversationTurn]:
    return [
        ConversationTurn(
            user_content=_truncate(clean_user_message(t.user_content), max_turn_chars),
            assistant_text=_truncate(t.assistant_text, max_turn_chars),
            tool_calls=sorted(set(t.tool_calls)),
        )
        for t in turns
    ]


def _render_batch(
    batch: Sequence[ConversationTurn],
    *,
    namespace: str,
    session_key: str,
    batch_index: int,
) -> str:
    lines = [
        "---",
        f"source: {namespace}",
        f"session: {session_key}",
        f"batch: {batch_index:03d}",
        f"turns: {len(batch)}",
        "---",
        "",
    ]
    # Turn numbers are batch-local: a batch's bytes must not depend on how many
    # batches precede it, or appending to a session would rewrite every file.
    for n, t in enumerate(batch, start=1):
        lines.append(f"## Turn {n}")
        lines.append("")
        if t.user_content:
            lines.append(f"**User:** {t.user_content}")
            lines.append("")
        if t.assistant_text:
            lines.append(f"**Assistant:** {t.assistant_text}")
            lines.append("")
        if t.tool_calls:
            lines.append(f"**Tools:** {', '.join(t.tool_calls)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def dump_session_turns(
    turns: Sequence[ConversationTurn],
    raw_root: Path,
    *,
    namespace: str,
    session_key: str,
    max_chars: int = 20_000,
    max_batches: int = 20,
    max_turn_chars: int = 4_000,
    dry_run: bool = False,
) -> list[Path]:
    """Write ``turns`` as markdown batches into ``raw/<namespace>/``.

    Files are ``<session_key>-<NNN>.md``. ``chunk_turns`` is called with
    ``overlap=0`` so the batches partition the conversation: with no repeated
    turns, appending to a live session only changes the *last* file, and every
    earlier chunk stays in the extract cache.

    ``max_batches`` caps a session as a *prefix* — never a rolling window, which
    would rewrite (and so re-extract) every file on every cycle.
    """
    if not turns:
        return []

    key = safe_key(session_key)
    manifest_key = f"{namespace}:{key}"
    manifest = load_import_manifest(raw_root, namespace)
    digest = turns_digest(turns)
    if not dry_run and manifest.get(manifest_key) == digest:
        return []

    batches = chunk_turns(_clip_turns(turns, max_turn_chars), max_chars, overlap=0)
    batches = batches[:max_batches]
    dest_dir = raw_root / namespace
    written: list[Path] = []

    for i, batch in enumerate(batches, start=1):
        dest = dest_dir / f"{key}-{i:03d}.md"
        rendered = _render_batch(
            batch, namespace=namespace, session_key=key, batch_index=i
        )
        if dry_run:
            written.append(dest)
            continue
        # Second guard, per file: even with a lost manifest, an unchanged prefix
        # must not touch mtime — the daemon watches raw/ for mtime changes.
        if dest.exists() and dest.read_text(encoding="utf-8") == rendered:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        written.append(dest)

    if not dry_run:
        manifest[manifest_key] = digest
        save_import_manifest(raw_root, namespace, manifest)

    return written


def prune_sessions(raw_root: Path, namespace: str, *, retain: int) -> list[Path]:
    """Drop all but the ``retain`` newest sessions from ``raw/<namespace>/``.

    Only ever called on a ``-session`` namespace: those files are machine-dumped
    and reproducible, whereas ``-memory`` files are agent-authored and would be
    lost for good.
    """
    if not namespace.endswith("-session"):
        raise ValueError(f"refusing to prune non-session namespace: {namespace}")

    dest_dir = raw_root / namespace
    if not dest_dir.is_dir():
        return []

    by_key: dict[str, list[Path]] = {}
    for path in sorted(dest_dir.glob("*-[0-9][0-9][0-9].md")):
        by_key.setdefault(path.name.rsplit("-", 1)[0], []).append(path)

    if len(by_key) <= retain:
        return []

    ranked = sorted(
        by_key.items(),
        key=lambda kv: (max(p.stat().st_mtime for p in kv[1]), kv[0]),
        reverse=True,
    )
    manifest = load_import_manifest(raw_root, namespace)
    removed: list[Path] = []
    for key, paths in ranked[retain:]:
        for path in paths:
            path.unlink()
            removed.append(path)
        manifest.pop(f"{namespace}:{key}", None)
    save_import_manifest(raw_root, namespace, manifest)
    return removed
