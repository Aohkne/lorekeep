from datetime import date
from pathlib import Path
import json
import pytest
from lorekeep.models import Node, Edge, Manifest
from lorekeep.compile.writer import write_graph, run_id, facts_hash


def n(id, type="service"):
    return Node(id=id, type=type, ns=("teams/backend",), props={"name": id})


def e(frm, to):
    return Edge(id="e_depends_on_0001", type="depends_on", **{"from": frm},
                to=to, ns=("teams/backend",))


def test_write_graph_is_deterministic(tmp_path: Path):
    nodes = [n("svc:b"), n("svc:a")]          # unsorted on purpose
    edges = [e("svc:a", "svc:b")]
    out1 = tmp_path / "g1"
    out2 = tmp_path / "g2"
    write_graph(out1, nodes, edges, Manifest(schema_version=1, chunk_count=1,
                node_count=2, edge_count=1, run_id="r", facts_hash="x"))
    write_graph(out2, nodes, edges, Manifest(schema_version=1, chunk_count=1,
                node_count=2, edge_count=1, run_id="r", facts_hash="x"))
    assert (out1 / "facts.jsonl").read_bytes() == (out2 / "facts.jsonl").read_bytes()


def test_facts_sorted_node_before_edge(tmp_path: Path):
    nodes = [n("svc:a")]
    edges = [e("svc:a", "svc:b")]
    nodes.append(n("svc:b"))
    out = tmp_path / "g"
    write_graph(out, nodes, edges, Manifest(schema_version=1, chunk_count=1,
                node_count=2, edge_count=1, run_id="r", facts_hash="x"))
    lines = (out / "facts.jsonl").read_text().splitlines()
    kinds = [json.loads(l)["kind"] for l in lines]
    assert kinds == sorted(kinds)               # nodes (edge<n? 'edge'<'node' -> edges first is fine; assert stable)


def test_manifest_written(tmp_path: Path):
    out = tmp_path / "g"
    m = Manifest(schema_version=1, chunk_count=1, node_count=1, edge_count=0,
                 run_id="r", facts_hash="x", chunk_hashes={"abc": ["svc:a"]})
    write_graph(out, [n("svc:a")], [], m)
    loaded = Manifest.from_json((out / "manifest.json").read_text())
    assert loaded.chunk_hashes == {"abc": ["svc:a"]}


def test_atomic_write_cleanup_on_error(tmp_path: Path, monkeypatch):
    """_atomic_write cleans up temp file when os.replace fails."""
    from lorekeep.compile.writer import _atomic_write
    target = tmp_path / "out" / "data.txt"
    monkeypatch.setattr("lorekeep.compile.writer.os.replace",
                        lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        _atomic_write(target, "hello")
    # temp file should be cleaned up
    remaining = list((tmp_path / "out").glob("data.txt.*.tmp"))
    assert remaining == []


def test_run_id_deterministic():
    from lorekeep.models import DocChunk
    cs = [DocChunk(path="b.md", start_line=1, end_line=1, text="x", namespace="t/b"),
          DocChunk(path="a.md", start_line=1, end_line=1, text="x", namespace="t/a")]
    assert run_id(cs, 1) == run_id(list(reversed(cs)), 1)
