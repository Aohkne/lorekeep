"""FastMCP server exposing the scoped temporal graph, read-only.

Tools are plain module functions using a module-global ScopedGraph set by
configure(). @mcp.tool() registers them with FastMCP but they remain directly
callable, so tests invoke them without the MCP transport.
"""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from lorekeep.models import Schema
from lorekeep.perm.ns import ScopedGraph
from lorekeep.schema_io import load_schema
from lorekeep.store.graph import GraphStore, parse_date

mcp = FastMCP("lorekeep")

_state: dict = {}          # graph_dir, allowed_ns, schema_path, facts_mtime
_scope: ScopedGraph | None = None
_schema: Schema | None = None


def configure(graph_dir, allowed_ns, schema_path=None, fts_path=None) -> None:
    """Set the graph location + scope, then build. Safe to call again to refresh."""
    _state["graph_dir"] = Path(graph_dir)
    _state["allowed_ns"] = list(allowed_ns)
    _state["schema_path"] = Path(schema_path) if schema_path else None
    _rebuild()


def _rebuild() -> None:
    """(Re)load the graph + schema from disk into a fresh ScopedGraph."""
    global _scope, _schema
    facts = _state["graph_dir"] / "facts.jsonl"
    store = GraphStore.from_jsonl(facts)
    sp = _state.get("schema_path")
    _schema = load_schema(sp) if sp else None
    _scope = ScopedGraph(store, _state["allowed_ns"])
    _state["facts_mtime"] = facts.stat().st_mtime if facts.exists() else 0


def _require() -> ScopedGraph:
    """Return the scoped graph, lazy-reloading if facts.jsonl changed on disk."""
    if not _state:
        raise RuntimeError("mcp_server not configured; call configure() first")
    facts = _state["graph_dir"] / "facts.jsonl"
    mtime = facts.stat().st_mtime if facts.exists() else 0
    if _scope is None or mtime != _state.get("facts_mtime"):
        _rebuild()
    return _scope


@mcp.tool()
def get_node(id: str) -> dict:
    """Return a node by id (props + provenance), or error if absent/out of scope."""
    node = _require().get_node(id)
    if node is None:
        return {"error": "not found or out of scope"}
    return node.model_dump(mode="json", by_alias=True)


@mcp.tool()
def neighbors(id: str, edge_type: str = "", depth: int = 1) -> dict:
    """Traverse neighbors up to depth (both directions), scoped to the caller."""
    scoped = _require()
    depth = max(1, min(int(depth), 5))   # bound BFS cost; 5 hops spans any realistic graph
    res = scoped.neighbors(id, edge_type or None, depth)
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in res["nodes"]],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in res["edges"]],
    }


@mcp.tool()
def schema() -> dict:
    """Return the graph schema (node/edge types)."""
    if _schema is None:
        return {"error": "no schema loaded"}
    return _schema.model_dump(mode="json", by_alias=True)


@mcp.tool()
def list_namespaces() -> list:
    """Namespaces visible to this caller."""
    return _require().list_namespaces()


@mcp.tool()
def at_time(time: str) -> dict:
    """Snapshot of facts valid at an ISO date (half-open [valid_from, valid_to))."""
    scoped = _require()
    nodes, edges = scoped.snapshot(parse_date(time))
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in nodes],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in edges],
    }


@mcp.tool()
def history(id: str) -> list:
    """All versions of an entity + edges touching it, ordered by valid_from."""
    return _require().history(id)


@mcp.tool()
def changes(from_t: str, to_t: str) -> dict:
    """Edges whose validity began or ended within [from_t, to_t)."""
    return _require().changes(parse_date(from_t), parse_date(to_t))


@mcp.tool()
def search(query: str, limit: int = 10) -> list:
    """Text search over node ids/props, scoped to the caller."""
    return _require().search(query, limit)


if __name__ == "__main__":
    mcp.run()
