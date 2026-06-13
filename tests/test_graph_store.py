from pathlib import Path
from laputa.store.graph import GraphStore


def store_from_gold(fixtures: Path) -> GraphStore:
    import shutil, tempfile
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    return GraphStore.from_jsonl(d / "facts.jsonl")


def test_load_and_get_node(fixtures: Path):
    g = store_from_gold(fixtures)
    assert g.node_ids() == {"svc:payments-api", "svc:auth", "team:backend", "dec:adr-007"}
    n = g.get_node("svc:payments-api")
    assert n is not None
    assert n.type == "service"
    assert n.props["name"] == "payments-api"
    assert g.get_node("missing") is None


def test_all_nodes_all_edges(fixtures: Path):
    g = store_from_gold(fixtures)
    assert len(g.all_nodes()) == 4
    assert len(g.all_edges()) == 2
