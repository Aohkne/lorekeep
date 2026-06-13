from pathlib import Path
from laputa.eval.gold import load_gold, load_compiled, node_key, edge_key


def test_load_gold(tmp_path: Path, fixtures: Path):
    facts = load_gold(fixtures / "gold")
    ids = {f.id for f in facts}
    assert "svc:payments-api" in ids
    assert len(facts) == 6


def test_node_key_uses_type_and_name():
    from laputa.models import Node
    n = Node(id="svc:x", type="service", ns=("t/b",), props={"name": "auth"})
    assert node_key(n) == ("service", "auth")


def test_edge_key_uses_type_and_endpoint_names():
    from laputa.models import Node, Edge
    nodes = {"svc:a": Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
             "svc:b": Node(id="svc:b", type="service", ns=("t/b",), props={"name": "b"})}
    e = Edge(id="e1", type="depends_on", **{"from": "svc:a"}, to="svc:b", ns=("t/b",))
    assert edge_key(e, nodes) == ("depends_on", "a", "b")


from laputa.eval.construction import precision_recall_f1, extraction_report


def test_prf1_perfect():
    p, r, f1 = precision_recall_f1({1, 2, 3}, {1, 2, 3})
    assert (p, r, f1) == (1.0, 1.0, 1.0)


def test_prf1_partial():
    p, r, f1 = precision_recall_f1({1, 2, 3}, {2, 3, 4})
    assert p == 2/3 and r == 2/3 and abs(f1 - 2/3) < 1e-9


def test_extraction_report_against_gold(tmp_path: Path, fixtures: Path):
    # compile with the canned fixture response, then score vs gold
    import json as _json
    from laputa.pipeline import compile_graph
    from laputa.compile.providers import FakeProvider
    from laputa.models import Schema
    from laputa.eval.gold import load_gold

    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/payments.md").write_text(
        (fixtures / "raw/teams/backend/payments.md").read_text())
    schema = Schema.load(_json.loads((fixtures / "schema.json").read_text()))
    canned = _json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth"},
            {"id": "team:backend", "type": "team", "name": "team-backend"},
            {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
             "props": {"title": "payments-api adopts internal signing"}},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
            {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
        ],
        "aliases": {},
    })
    compile_graph(raw, tmp_path / "g", schema, FakeProvider([canned]), tmp_path / "c.json")
    report = extraction_report(tmp_path / "g", fixtures / "gold")
    assert report["nodes"]["f1"] == 1.0
    assert report["edges"]["f1"] == 1.0
