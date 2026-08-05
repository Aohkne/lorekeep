"""Journal: append-only writer + loader for agent-proposed facts.

Agent-proposed facts land in pending/<ns>/journal.jsonl (or pending/<agent>/)
as append-only JSONL. Facts enter facts.jsonl after the resolve pass.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from lorekeep.models import JournalEntry

log = logging.getLogger("lorekeep.journal")


def journal_path(pending_dir: Path, ns: str) -> Path:
    ns_path = pending_dir / ns
    ns_path.mkdir(parents=True, exist_ok=True)
    return ns_path / "journal.jsonl"


@contextmanager
def _journal_lock(path: Path):
    """Cross-process lock shared by append and status-rewrite operations."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def resolve_lock(pending_dir: Path):
    """Serialize the full graph read→merge→write→status transaction."""
    with _journal_lock(Path(pending_dir) / ".resolve"):
        yield


def append_journal(pending_dir: Path, entry: JournalEntry, ns: str) -> JournalEntry:
    path = journal_path(pending_dir, ns)
    line = json.dumps(entry.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) + "\n"
    with _journal_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    log.info("journal entry appended", extra={"event": "journal.append"})
    return entry


def load_journals(pending_dir: Path) -> list[JournalEntry]:
    if not pending_dir.exists():
        return []
    entries: list[JournalEntry] = []
    for journal_file in sorted(pending_dir.rglob("journal.jsonl")):
        try:
            text = journal_file.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning(
                "journal file unreadable error_type=%s", type(exc).__name__,
                extra={"event": "journal.read_failed"},
            )
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(JournalEntry.model_validate(d))
            except Exception as exc:
                log.warning(
                    "invalid journal line line=%s error_type=%s",
                    line_number, type(exc).__name__,
                    extra={"event": "journal.invalid_line"},
                )
                continue
    return entries


def update_journal_status(pending_dir: Path, ns: str,
                          entry_key_set: set[str],
                          new_status: str) -> None:
    path = journal_path(pending_dir, ns)
    if not path.exists():
        return
    with _journal_lock(path):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        updated: list[str] = []
        for line_text in lines:
            line_text = line_text.strip()
            if not line_text:
                updated.append("")
                continue
            try:
                d = json.loads(line_text)
                key = d.get("entry_id") or d.get("proposed_at")
                if key in entry_key_set and d.get("status") == "pending":
                    d["status"] = new_status
                updated.append(json.dumps(d, sort_keys=True, ensure_ascii=False))
            except Exception as exc:
                log.warning(
                    "journal status line preserved line=%s error_type=%s",
                    len(updated) + 1, type(exc).__name__,
                    extra={"event": "journal.status_invalid_line"},
                )
                updated.append(line_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(updated) + ("\n" if updated else ""))
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
