import shutil, tempfile
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
    r = ms.search("payments")
    assert "svc:payments-api" in r


def test_neighbors_depth_is_capped(fixtures: Path):
    setup_server(fixtures, ["backend"])
    shallow = ms.neighbors("svc:payments-api", depth=1)
    deep = ms.neighbors("svc:payments-api", depth=10_000)   # would traverse whole graph unbounded
    assert {n["id"] for n in deep["nodes"]} == {n["id"] for n in shallow["nodes"]}


def test_context_status_reports_total_and_scoped(fixtures: Path):
    """context('status') distinguishes scoped from total graph size."""
    setup_server(fixtures, ["backend"])
    r = ms.context("status")["status"]
    assert r["nodes"] == 4                          # scoped (all backend)
    assert r["total_nodes"] == 4                    # total (same — one ns in fixture)
    assert r["total_edges"] == 2
    assert "backend" in r["all_namespaces"]


def test_context_status_shows_total_when_scope_is_empty(fixtures: Path):
    """When scope matches nothing, total stats reveal the graph isn't empty."""
    setup_server(fixtures, ["nonexistent-ns"])
    r = ms.context("status")["status"]
    assert r["nodes"] == 0                          # nothing in scope
    assert r["total_nodes"] == 4                    # but graph has data
    assert r["total_edges"] == 2
