import shutil, tempfile
from pathlib import Path
import laputa.mcp_server as ms


def setup_server(fixtures: Path, allowed):
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=allowed, schema_path=fixtures / "schema.json")
    return d


def test_get_node_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.get_node("svc:payments-api")
    assert r["id"] == "svc:payments-api"
    assert r["type"] == "service"


def test_get_node_hidden_returns_error(fixtures: Path):
    setup_server(fixtures, ["teams/frontend"])
    r = ms.get_node("svc:payments-api")
    assert "error" in r


def test_neighbors_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.neighbors("svc:payments-api", depth=1)
    assert {n["id"] for n in r["nodes"]} == {"svc:payments-api", "svc:auth"}


def test_schema_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.schema()
    assert "node_types" in r and "service" in r["node_types"]


def test_list_namespaces_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    assert ms.list_namespaces() == ["public", "teams/backend"]
