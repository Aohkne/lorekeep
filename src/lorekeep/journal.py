"""Journal: append-only writer + loader for agent-proposed facts.

Agent-proposed facts land in pending/<ns>/journal.jsonl (or pending/<agent>/)
as append-only JSONL. Facts enter facts.jsonl after the resolve pass.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from lorekeep.models import JournalEntry


def journal_path(pending_dir: Path, ns: str) -> Path:
    ns_path = pending_dir / ns
    ns_path.mkdir(parents=True, exist_ok=True)
    return ns_path / "journal.jsonl"


def append_journal(pending_dir: Path, entry: JournalEntry, ns: str) -> JournalEntry:
    path = journal_path(pending_dir, ns)
    line = json.dumps(entry.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return entry


def load_journals(pending_dir: Path) -> list[JournalEntry]:
    if not pending_dir.exists():
        return []
    entries: list[JournalEntry] = []
    for journal_file in sorted(pending_dir.rglob("journal.jsonl")):
        try:
            text = journal_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(JournalEntry.model_validate(d))
            except Exception:
                continue
    return entries


def update_journal_status(pending_dir: Path, ns: str,
                          proposed_at_set: set[str],
                          new_status: str) -> None:
    path = journal_path(pending_dir, ns)
    if not path.exists():
        return
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
            if d.get("proposed_at") in proposed_at_set and d.get("status") == "pending":
                d["status"] = new_status
            updated.append(json.dumps(d, sort_keys=True, ensure_ascii=False))
        except Exception:
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
