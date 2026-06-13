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
