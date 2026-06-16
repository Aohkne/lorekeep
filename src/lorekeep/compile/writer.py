"""Writer: emit deterministic facts.jsonl + manifest.json.

Determinism = facts sorted by (kind, type, id), JSON keys sorted, stable
separators. Re-compiling unchanged input yields byte-identical output.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from lorekeep.models import DocChunk, Edge, Manifest, Node


def _sort_key(fact: Node | Edge) -> tuple[str, str, str]:
    return (fact.kind, fact.type, fact.id)


def _atomic_write(path: Path, data: str) -> None:
    """Write data to path atomically: stage a temp file then os.replace onto it.

    Prevents a torn read when the MCP server lazy-reloads facts.jsonl mid-write
    (compile truncating the file while a query reads it). os.replace is atomic
    when src and dst share a filesystem, which holds for a sibling temp file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_graph(
    out_dir: Path, nodes: list[Node], edges: list[Edge], manifest: Manifest,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = sorted(nodes + edges, key=_sort_key)
    lines = [f.to_json_line() for f in facts]
    text = "\n".join(lines) + ("\n" if lines else "")
    _atomic_write(out_dir / "facts.jsonl", text)
    _atomic_write(out_dir / "manifest.json", manifest.to_json())


def run_id(chunks: list[DocChunk], schema_version: int) -> str:
    h = hashlib.sha256()
    h.update(str(schema_version).encode("utf-8"))
    for c in sorted(chunks, key=lambda c: (c.path, c.start_line)):
        h.update(c.hash.encode("utf-8"))
    return h.hexdigest()[:16]


def facts_hash(out_dir: Path) -> str:
    raw = (out_dir / "facts.jsonl").read_bytes()
    return hashlib.sha256(raw).hexdigest()[:16]
