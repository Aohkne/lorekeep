import json
import shutil
import tempfile
from datetime import date
from pathlib import Path
import lorekeep.mcp_server as ms


def setup_server(fixtures: Path, allowed):
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=allowed, schema_path=fixtures / "schema.json")
    return d


def test_get_node_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.get_node("svc:payments-api")
    assert r["id"] == "svc:payments-api"
    assert r["type"] == "service"


def test_get_node_hidden_returns_error(fixtures: Path):
    setup_server(fixtures, ["teams/frontend"])
    r = ms.get_node("svc:payments-api")
    assert "error" in r


def test_neighbors_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.neighbors("svc:payments-api", depth=1)
    assert {n["id"] for n in r["nodes"]} == {"svc:payments-api", "svc:auth"}


def test_context_schema(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.context("schema")["schema"]
    assert "node_types" in r and "service" in r["node_types"]


def test_context_namespaces(fixtures: Path):
    setup_server(fixtures, ["backend"])
    assert ms.context("namespaces")["namespaces"] == ["backend", "public"]


def test_temporal_at_time(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.temporal_query("at_time", {"time": "2025-02-28"})
    types = {e["type"] for e in r["edges"]}
    assert "depends_on" in types
    r2 = ms.temporal_query("at_time", {"time": "2025-03-01"})
    assert "depends_on" not in {e["type"] for e in r2["edges"]}


def test_temporal_history(fixtures: Path):
    setup_server(fixtures, ["backend"])
    h = ms.temporal_query("history", {"id": "svc:payments-api"})["items"]
    assert h[0]["kind"] == "node"


def test_temporal_changes(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.temporal_query(
        "changes", {"from_time": "2024-01-01", "to_time": "2025-04-01"},
    )
    assert "depends_on" in {e["type"] for e in r["began"]}


def test_search_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.search("payments", as_of="all")
    assert "svc:payments-api" in r["nodes"]
    facts = r["facts"]
    assert any(f["id"] == "e_dep_1" for f in facts)
    signing = ms.search("uses auth to validate", scope="facts", as_of="all")
    assert signing["nodes"] == []
    assert any(f["id"] == "e_dep_1" for f in signing["facts"])
    nodes_only = ms.search("payments", scope="nodes", as_of="all")
    assert nodes_only["facts"] == []
    facts_only = ms.search("uses auth to validate", scope="facts", as_of="all")
    assert facts_only["nodes"] == []
    assert facts_only["facts"]


def test_search_hides_expired_facts_by_default(fixtures: Path):
    setup_server(fixtures, ["backend"])
    today = ms.search("auth")
    assert not any(f["id"] == "e_dep_1" for f in today["facts"])
    hist = ms.search("auth", as_of="all")
    assert any(f["id"] == "e_dep_1" for f in hist["facts"])
    snap = ms.search("auth", as_of="2025-02-01")
    assert any(f["id"] == "e_dep_1" for f in snap["facts"])
    ended = ms.search("auth", as_of="2025-03-01")
    assert not any(f["id"] == "e_dep_1" for f in ended["facts"])


def test_search_rejects_bad_as_of(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.search("payments", as_of="last-week")
    assert "error" in r


def _write_facts(graph: Path, rows: list[dict]) -> None:
    graph.mkdir(parents=True, exist_ok=True)
    (graph / "facts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )


def test_search_packs_typed_one_hop_neighbors(tmp_path: Path, fixtures: Path):
    graph = tmp_path / "graph"
    _write_facts(graph, [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["backend"],
         "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["backend"],
         "props": {"name": "b"}},
        {"kind": "node", "id": "svc:c", "type": "service", "ns": ["backend"],
         "props": {"name": "c"}},
        {"kind": "node", "id": "svc:d", "type": "service", "ns": ["backend"],
         "props": {"name": "d"}},
        {"kind": "edge", "id": "e_seed", "type": "depends_on",
         "from": "svc:a", "to": "svc:b", "ns": ["backend"],
         "props": {"description": "token handshake"}},
        {"kind": "edge", "id": "e_part", "type": "part_of",
         "from": "svc:a", "to": "svc:c", "ns": ["backend"],
         "props": {"description": "a belongs to c"}},
        {"kind": "edge", "id": "e_rel", "type": "relates_to",
         "from": "svc:a", "to": "svc:d", "ns": ["backend"],
         "props": {"description": "vague link"}},
    ])
    ms.configure(
        graph_dir=graph, allowed_ns=["backend"], schema_path=fixtures / "schema.json",
    )
    r = ms.search("handshake", as_of="all", scope="facts")
    seed = next(f for f in r["facts"] if f["id"] == "e_seed")
    nids = {n["id"] for n in seed["neighbors"]}
    assert "e_part" in nids
    assert "e_rel" not in nids
    assert all("neighbors" not in n for n in seed["neighbors"])


def test_search_center_id_ranks_nearby_facts(tmp_path: Path, fixtures: Path):
    graph = tmp_path / "graph"
    _write_facts(graph, [
        {"kind": "node", "id": "svc:hub", "type": "service", "ns": ["backend"],
         "props": {"name": "hub"}},
        {"kind": "node", "id": "svc:leaf", "type": "service", "ns": ["backend"],
         "props": {"name": "leaf"}},
        {"kind": "node", "id": "svc:x", "type": "service", "ns": ["backend"],
         "props": {"name": "x"}},
        {"kind": "node", "id": "svc:y", "type": "service", "ns": ["backend"],
         "props": {"name": "y"}},
        {"kind": "edge", "id": "e_far", "type": "depends_on",
         "from": "svc:x", "to": "svc:y", "ns": ["backend"],
         "props": {"description": "token handshake"}},
        {"kind": "edge", "id": "e_hub", "type": "depends_on",
         "from": "svc:hub", "to": "svc:leaf", "ns": ["backend"],
         "props": {"description": "token handshake"}},
    ])
    ms.configure(
        graph_dir=graph, allowed_ns=["backend"], schema_path=fixtures / "schema.json",
    )
    r = ms.search(
        "handshake", as_of="all", scope="facts", center_id="svc:hub",
    )
    assert r["facts"][0]["id"] == "e_hub"


def test_parse_search_as_of(monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 23)

    monkeypatch.setattr(ms, "date", FakeDate)
    assert ms._parse_search_as_of("") == date(2026, 8, 23)
    assert ms._parse_search_as_of("   ") == date(2026, 8, 23)
    assert ms._parse_search_as_of("all") is None
    assert ms._parse_search_as_of("ALL") is None
    assert ms._parse_search_as_of("2025-02-01") == date(2025, 2, 1)
    assert ms._parse_search_as_of("2025-02-01T12:00:00Z") == date(2025, 2, 1)


def test_search_as_of_all_is_case_insensitive(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.search("uses auth to validate", scope="facts", as_of="ALL")
    assert any(f["id"] == "e_dep_1" for f in r["facts"])
    snap = ms.search("uses auth to validate", scope="facts", as_of="2025-02-01T00:00:00Z")
    assert any(f["id"] == "e_dep_1" for f in snap["facts"])


def test_search_facts_include_neighbors_key(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.search("signing", as_of="all", scope="facts")
    assert r["facts"]
    for fact in r["facts"]:
        assert isinstance(fact["neighbors"], list)
        assert all("neighbors" not in hop for hop in fact["neighbors"])


def test_search_hides_expired_nodes_by_default(tmp_path: Path, fixtures: Path):
    graph = tmp_path / "graph"
    _write_facts(graph, [
        {"kind": "node", "id": "svc:live", "type": "service", "ns": ["backend"],
         "props": {"name": "live-token"}},
        {"kind": "node", "id": "svc:old", "type": "service", "ns": ["backend"],
         "valid_from": "2020-01-01", "valid_to": "2021-01-01",
         "props": {"name": "old-token"}},
    ])
    ms.configure(
        graph_dir=graph, allowed_ns=["backend"], schema_path=fixtures / "schema.json",
    )
    today = ms.search("token", scope="nodes")
    assert "svc:live" in today["nodes"]
    assert "svc:old" not in today["nodes"]
    hist = ms.search("token", scope="nodes", as_of="all")
    assert "svc:old" in hist["nodes"]


def test_search_neighbors_respect_as_of(tmp_path: Path, fixtures: Path):
    graph = tmp_path / "graph"
    _write_facts(graph, [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["backend"],
         "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["backend"],
         "props": {"name": "b"}},
        {"kind": "node", "id": "svc:c", "type": "service", "ns": ["backend"],
         "props": {"name": "c"}},
        {"kind": "edge", "id": "e_seed", "type": "depends_on",
         "from": "svc:a", "to": "svc:b", "ns": ["backend"],
         "props": {"description": "token handshake"}},
        {"kind": "edge", "id": "e_dead", "type": "part_of",
         "from": "svc:a", "to": "svc:c", "ns": ["backend"],
         "valid_from": "2020-01-01", "valid_to": "2021-01-01",
         "props": {"description": "expired membership"}},
    ])
    ms.configure(
        graph_dir=graph, allowed_ns=["backend"], schema_path=fixtures / "schema.json",
    )
    today = ms.search("handshake", scope="facts")
    seed = next(f for f in today["facts"] if f["id"] == "e_seed")
    assert seed["neighbors"] == []
    hist = ms.search("handshake", scope="facts", as_of="all")
    hist_seed = next(f for f in hist["facts"] if f["id"] == "e_seed")
    assert {n["id"] for n in hist_seed["neighbors"]} == {"e_dead"}


def test_search_neighbors_do_not_leak_hidden_namespace(tmp_path: Path, fixtures: Path):
    graph = tmp_path / "graph"
    _write_facts(graph, [
        {"kind": "node", "id": "svc:a", "type": "service", "ns": ["backend"],
         "props": {"name": "a"}},
        {"kind": "node", "id": "svc:b", "type": "service", "ns": ["backend"],
         "props": {"name": "b"}},
        {"kind": "node", "id": "svc:secret", "type": "service", "ns": ["secret"],
         "props": {"name": "secret"}},
        {"kind": "edge", "id": "e_seed", "type": "depends_on",
         "from": "svc:a", "to": "svc:b", "ns": ["backend"],
         "props": {"description": "token handshake"}},
        {"kind": "edge", "id": "e_hid", "type": "part_of",
         "from": "svc:a", "to": "svc:secret", "ns": ["secret"],
         "props": {"description": "hidden membership"}},
    ])
    ms.configure(
        graph_dir=graph, allowed_ns=["backend"], schema_path=fixtures / "schema.json",
    )
    r = ms.search("handshake", as_of="all", scope="facts")
    seed = next(f for f in r["facts"] if f["id"] == "e_seed")
    assert seed["neighbors"] == []
    assert not any(f["id"] == "e_hid" for f in r["facts"])


def test_search_rejects_bad_scope(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.search("payments", scope="edges")  # type: ignore[arg-type]
    assert "error" in r


def test_neighbors_depth_is_capped(fixtures: Path):
    setup_server(fixtures, ["backend"])
    shallow = ms.neighbors("svc:payments-api", depth=1)
    deep = ms.neighbors("svc:payments-api", depth=10_000)   # would traverse whole graph unbounded
    assert {n["id"] for n in deep["nodes"]} == {n["id"] for n in shallow["nodes"]}


def test_context_status_reports_only_scoped_graph_metadata(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.context("status")["status"]
    assert r["nodes"] == 4
    assert r["edges"] == 2
    assert r["namespaces"] == ["backend"]
    assert "total_nodes" not in r
    assert "total_edges" not in r
    assert "all_namespaces" not in r


def test_context_status_does_not_leak_hidden_namespace(fixtures: Path):
    d = setup_server(fixtures, ["backend"])
    hidden = {
        "kind": "node", "id": "svc:secret", "type": "service",
        "ns": ["secret-project"], "props": {"name": "secret"},
    }
    with (d / "facts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(hidden) + "\n")
    ms.configure(
        graph_dir=d,
        allowed_ns=["backend"],
        schema_path=fixtures / "schema.json",
    )

    r = ms.context("status")["status"]
    assert r["nodes"] == 4
    assert r["namespaces"] == ["backend"]
    assert "secret-project" not in str(r)
