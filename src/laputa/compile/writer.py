"""Writer: emit deterministic facts.jsonl + manifest.json.

Determinism = facts sorted by (kind, type, id), JSON keys sorted, stable
separators. Re-compiling unchanged input yields byte-identical output.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from laputa.models import DocChunk, Edge, Manifest, Node


def _sort_key(fact: Node | Edge) -> tuple[str, str, str]:
    return (fact.kind, fact.type, fact.id)


def write_graph(
    out_dir: Path, nodes: list[Node], edges: list[Edge], manifest: Manifest,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = sorted(nodes + edges, key=_sort_key)
    lines = [f.to_json_line() for f in facts]
    text = "\n".join(lines) + ("\n" if lines else "")
    (out_dir / "facts.jsonl").write_text(text, encoding="utf-8")
    (out_dir / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")


def run_id(chunks: list[DocChunk], schema_version: int) -> str:
    h = hashlib.sha256()
    h.update(str(schema_version).encode("utf-8"))
    for c in sorted(chunks, key=lambda c: (c.path, c.start_line)):
        h.update(c.hash.encode("utf-8"))
    return h.hexdigest()[:16]


def facts_hash(out_dir: Path) -> str:
    raw = (out_dir / "facts.jsonl").read_bytes()
    return hashlib.sha256(raw).hexdigest()[:16]
