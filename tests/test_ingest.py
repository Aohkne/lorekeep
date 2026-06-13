from pathlib import Path
from laputa.compile.ingest import ingest, namespace_for


def test_namespace_from_path(tmp_path: Path):
    raw = tmp_path / "raw"
    f = raw / "teams" / "backend" / "payments.md"
    f.parent.mkdir(parents=True)
    f.write_text("x\n")
    assert namespace_for(raw, f) == "teams/backend"


def test_ingest_splits_into_line_chunks(tmp_path: Path):
    raw = tmp_path / "raw"
    f = raw / "teams" / "backend" / "a.md"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(str(i) for i in range(150)))  # 150 lines
    chunks = ingest(raw, chunk_lines=60)
    assert len(chunks) == 3                       # 60 + 60 + 30
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 60
    assert chunks[0].namespace == "teams/backend"
    assert chunks[0].src == "teams/backend/a.md:1"
    assert chunks[1].start_line == 61


def test_ingest_sorted_and_skips_dirs(tmp_path: Path):
    raw = tmp_path / "raw"
    (raw / "teams" / "backend").mkdir(parents=True)
    (raw / "teams" / "backend" / "b.md").write_text("b\n")
    (raw / "teams" / "backend" / "a.md").write_text("a\n")
    chunks = ingest(raw)
    paths = [c.path for c in chunks]
    assert paths == sorted(paths)
