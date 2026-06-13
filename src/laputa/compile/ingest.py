"""Ingest: raw markdown files -> DocChunks with provenance."""
from __future__ import annotations

from pathlib import Path

from laputa.models import DocChunk


def namespace_for(raw_root: Path, path: Path) -> str:
    rel = path.relative_to(raw_root)
    parts = rel.parts
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
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
    chunks: list[DocChunk] = []
    for p in sorted(raw_root.glob(glob)):
        if p.is_file():
            chunks.extend(ingest_file(raw_root, p, chunk_lines))
    return chunks
