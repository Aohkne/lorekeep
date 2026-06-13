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


from laputa.store.graph import parse_date


def test_snapshot_includes_active_edge_before_valid_to(fixtures: Path):
    g = store_from_gold(fixtures)
    nodes, edges = g.snapshot(parse_date("2025-02-28"))
    edge_types = {e.type for e in edges}
    assert "depends_on" in edge_types      # still active on 2025-02-28


def test_snapshot_excludes_edge_at_valid_to(fixtures: Path):
    g = store_from_gold(fixtures)
    # depends_on valid_to=2025-03-01 is exclusive -> absent at 2025-03-01
    _, edges = g.snapshot(parse_date("2025-03-01"))
    assert "depends_on" not in {e.type for e in edges}


def test_snapshot_before_valid_from_excludes_edge(fixtures: Path):
    g = store_from_gold(fixtures)
    _, edges = g.snapshot(parse_date("2024-01-14"))
    assert "depends_on" not in {e.type for e in edges}


def test_history_returns_node_and_touching_edges(fixtures: Path):
    g = store_from_gold(fixtures)
    h = g.history("svc:payments-api")
    kinds = [item["kind"] for item in h]
    assert kinds[0] == "node"
    assert "edge" in kinds
    # edges ordered by valid_from (None sorts first)
    edges = [it for it in h if it["kind"] == "edge"]
    assert edges[0]["type"] == "depends_on"


def test_changes_detects_began_and_ended(fixtures: Path):
    g = store_from_gold(fixtures)
    # depends_on valid_from=2024-01-15, valid_to=2025-03-01
    rep = g.changes(parse_date("2024-01-01"), parse_date("2025-04-01"))
    began_types = {e["type"] for e in rep["began"]}
    ended_types = {e["type"] for e in rep["ended"]}
    assert "depends_on" in began_types
    assert "depends_on" in ended_types


def test_changes_outside_window_empty(fixtures: Path):
    g = store_from_gold(fixtures)
    rep = g.changes(parse_date("2099-01-01"), parse_date("2099-02-01"))
    assert rep == {"began": [], "ended": []}


def test_store_search_scan(fixtures: Path):
    g = store_from_gold(fixtures)
    ids = g.search("payments")
    assert "svc:payments-api" in ids
