import shutil, tempfile
from pathlib import Path
import laputa.mcp_server as ms


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


def test_schema_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.schema()
    assert "node_types" in r and "service" in r["node_types"]


def test_list_namespaces_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    assert ms.list_namespaces() == ["backend", "public"]


def test_at_time_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.at_time("2025-02-28")
    types = {e["type"] for e in r["edges"]}
    assert "depends_on" in types
    r2 = ms.at_time("2025-03-01")
    assert "depends_on" not in {e["type"] for e in r2["edges"]}


def test_history_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    h = ms.history("svc:payments-api")
    assert h[0]["kind"] == "node"


def test_changes_tool(fixtures: Path):
    setup_server(fixtures, ["backend"])
    r = ms.changes("2024-01-01", "2025-04-01")
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
