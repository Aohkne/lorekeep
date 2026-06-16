from datetime import date
import json
from lorekeep.models import Node, Edge, DocChunk, Schema, Manifest


def test_node_serializes_with_sorted_keys():
    n = Node(id="svc:x", type="service", ns=("teams/backend",),
             valid_from=date(2024, 1, 15), props={"lang": "go"})
    d = json.loads(n.to_json_line())
    assert list(d.keys()) == sorted(d.keys())
    assert d["kind"] == "node"
    assert d["ns"] == ["teams/backend"]
    assert d["valid_to"] is None


def test_edge_uses_from_alias():
    e = Edge(id="e1", type="depends_on", **{"from": "a"}, to="b", ns=("teams/backend",))
    d = json.loads(e.to_json_line())
    assert d["from"] == "a"
    assert d["to"] == "b"
    assert "from_" not in d


def test_docchunk_hash_is_stable():
    c1 = DocChunk(path="raw/x.md", start_line=1, end_line=2, text="hello", namespace="teams/x")
    c2 = DocChunk(path="raw/x.md", start_line=1, end_line=2, text="hello", namespace="teams/x")
    assert c1.hash == c2.hash


def test_schema_loads_from_dict():
    s = Schema.load({
        "version": 1,
        "node_types": {"service": {"props": {"name": "string"}}},
        "edge_types": {"depends_on": {"from": "service", "to": "service"}},
    })
    assert s.version == 1
    assert "service" in s.node_types
    assert s.edge_types["depends_on"].from_ == "service"


def test_manifest_round_trips():
    m = Manifest(schema_version=1, chunk_count=1, node_count=2, edge_count=1,
                 run_id="abc", facts_hash="deadbeef", chunk_hashes={}, errors=[], quarantine=[])
    js = m.to_json()
    m2 = Manifest.from_json(js)
    assert m2.node_count == 2
