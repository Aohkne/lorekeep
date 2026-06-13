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


def test_out_and_in_edges(fixtures: Path):
    g = store_from_gold(fixtures)
    out = g.out_edges("svc:payments-api")
    assert len(out) == 1 and out[0].type == "depends_on"
    assert g.out_edges("svc:payments-api", edge_type="decided_by") == []
    inn = g.in_edges("svc:auth")
    assert len(inn) == 1 and inn[0].from_ == "svc:payments-api"


def test_neighbors_depth_one(fixtures: Path):
    g = store_from_gold(fixtures)
    nb = g.neighbors("svc:payments-api", depth=1)
    ids = {n.id for n in nb["nodes"]}
    assert ids == {"svc:payments-api", "svc:auth"}
    assert len(nb["edges"]) == 1


def test_neighbors_unknown_node(fixtures: Path):
    g = store_from_gold(fixtures)
    assert g.neighbors("nope") == {"nodes": [], "edges": []}
