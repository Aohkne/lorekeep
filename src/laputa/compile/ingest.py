"""Ingest: raw markdown files -> DocChunks with provenance."""
from __future__ import annotations

import sys
from pathlib import Path

from laputa.models import DocChunk


def namespace_for(raw_root: Path, path: Path) -> str:
    rel = path.relative_to(raw_root)
    parts = rel.parts
    if len(parts) >= 2:          # <dir>/<file> -> ns is the first directory
        return parts[0]
    return "public"


def ingest_file(raw_root: Path, path: Path, chunk_lines: int) -> list[DocChunk]:
    ns = namespace_for(raw_root, path)
    rel = str(path.relative_to(raw_root))
    lines = path.read_text(encoding="utf-8").splitlines()
    chunks: list[DocChunk] = []
    for start in range(0, len(lines), chunk_lines):
        block = lines[start:start + chunk_lines]
        if not any(line.strip() for line in block):
            continue
        chunks.append(DocChunk(
            path=rel,
            start_line=start + 1,
            end_line=start + len(block),
            text="\n".join(block),
            namespace=ns,
        ))
    return chunks


def ingest(raw_root: Path, glob: str = "**/*.md", chunk_lines: int = 60) -> list[DocChunk]:
    """Ingest files under raw_root into DocChunks.

    Any path whose resolved target escapes raw_root is skipped with a stderr
    warning. Everything under raw/ is sent to the LLM provider at compile, so a
    planted symlink (e.g. raw/x/leak.md -> ~/.ssh/id_rsa) must not exfiltrate
    files outside raw_root — fail closed.
    """
    root = raw_root.resolve()
    chunks: list[DocChunk] = []
    for p in sorted(raw_root.glob(glob)):
        if not p.is_file():
            continue
        if not p.resolve().is_relative_to(root):
            print(f"laputa: skip path outside raw_root (possible symlink): {p}",
                  file=sys.stderr)
            continue
        chunks.extend(ingest_file(raw_root, p, chunk_lines))
    return chunks
