"""FastMCP server exposing the scoped temporal graph with compact tool profiles.

Tools are plain module functions using a module-global ScopedGraph set by
``configure()``.  ``create_mcp()`` publishes either the compact default surface
or the full compatibility surface; every function remains directly callable so
tests and internal diagnostics do not depend on an MCP transport.

Write tools append to pending/<ns>/journal.jsonl; facts enter the graph on
the next resolve pass.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from lorekeep.journal import append_journal
from lorekeep.models import JournalEntry, Manifest, Schema
from lorekeep.perm.ns import ScopedGraph
from lorekeep.schema_io import load_schema
from lorekeep.store.graph import GraphStore, parse_date
from lorekeep.store.fts import FTSIndex

log = logging.getLogger("lorekeep.mcp")

MCP_PROFILES = ("core", "full")
DEFAULT_MCP_PROFILE = "core"

_state: dict = {}          # graph_dir, allowed_ns, schema_path, pending_dir, fts_path, facts_mtime
_scope: ScopedGraph | None = None
_schema: Schema | None = None
_manifest: Manifest | None = None
_fts: FTSIndex | None = None


def configure(graph_dir, allowed_ns, schema_path=None, fts_path=None, pending_dir=None) -> None:
    """Set the graph location + scope, then build. Safe to call again to refresh."""
    _state["graph_dir"] = Path(graph_dir)
    _state["allowed_ns"] = list(allowed_ns)
    _state["schema_path"] = Path(schema_path) if schema_path else None
    _state["pending_dir"] = Path(pending_dir) if pending_dir else None
    if fts_path:
        _state["fts_path"] = Path(fts_path)
    else:
        _state["fts_path"] = _state["graph_dir"] / "fts.sqlite"
    log.info(
        "configuring graph namespace_count=%s", len(_state["allowed_ns"]),
        extra={"event": "mcp.configure"},
    )
    _rebuild()


def _rebuild() -> None:
    """(Re)load the graph + schema + manifest + FTS from disk into a fresh ScopedGraph."""
    global _scope, _schema, _manifest, _fts
    facts = _state["graph_dir"] / "facts.jsonl"
    if not facts.exists():
        raise FileNotFoundError(
            f"facts.jsonl not found at {facts}. "
            "Run 'lorekeep compile' first to build the knowledge graph."
        )
    store = GraphStore.from_jsonl(facts)
    sp = _state.get("schema_path")
    _schema = load_schema(sp) if sp else None
    _scope = ScopedGraph(store, _state["allowed_ns"])
    manifest_path = _state["graph_dir"] / "manifest.json"
    if manifest_path.exists():
        _manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
    else:
        _manifest = None
    try:
        fts_path = _state.get("fts_path")
        if fts_path:
            _fts = FTSIndex(fts_path)
            _fts.build(store.all_nodes())
        else:
            _fts = None
    except Exception as exc:
        log.warning(
            "FTS unavailable; falling back to graph search error_type=%s",
            type(exc).__name__, extra={"event": "mcp.fts_fallback"},
        )
        _fts = None
    _state["facts_mtime"] = facts.stat().st_mtime if facts.exists() else 0
    log.info(
        "graph loaded node_count=%s edge_count=%s fts=%s",
        len(store.node_ids()), len(store.all_edges()), _fts is not None,
        extra={"event": "mcp.graph_loaded"},
    )


def _require() -> ScopedGraph:
    """Return the scoped graph, lazy-reloading if facts.jsonl changed on disk."""
    if not _state:
        raise RuntimeError("mcp_server not configured; call configure() first")
    facts = _state["graph_dir"] / "facts.jsonl"
    mtime = facts.stat().st_mtime if facts.exists() else 0
    if _scope is None or mtime != _state.get("facts_mtime"):
        log.info("facts changed; reloading graph", extra={"event": "mcp.reload"})
        _rebuild()
    return _scope


def get_node(id: str) -> dict:
    """Return a node by id (props + provenance), or error if absent/out of scope."""
    node = _require().get_node(id)
    if node is None:
        return {"error": "not found or out of scope"}
    return node.model_dump(mode="json", by_alias=True)


def neighbors(id: str, edge_type: str = "", depth: int = 1) -> dict:
    """Traverse neighbors up to depth (both directions), scoped to the caller."""
    scoped = _require()
    depth = max(1, min(int(depth), 5))   # bound BFS cost; 5 hops spans any realistic graph
    res = scoped.neighbors(id, edge_type or None, depth)
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in res["nodes"]],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in res["edges"]],
    }


def schema() -> dict:
    """Return the graph schema (node/edge types)."""
    if _schema is None:
        return {"error": "no schema loaded"}
    return _schema.model_dump(mode="json", by_alias=True)


def list_namespaces() -> list:
    """Namespaces visible to this caller."""
    return _require().list_namespaces()


def at_time(time: str) -> dict:
    """Snapshot of facts valid at an ISO date (half-open [valid_from, valid_to))."""
    scoped = _require()
    nodes, edges = scoped.snapshot(parse_date(time))
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in nodes],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in edges],
    }


def history(id: str) -> list:
    """All versions of an entity + edges touching it, ordered by valid_from."""
    return _require().history(id)


def changes(from_t: str, to_t: str) -> dict:
    """Edges whose validity began or ended within [from_t, to_t)."""
    return _require().changes(parse_date(from_t), parse_date(to_t))


def search(query: str, limit: int = 10) -> list:
    """Text search over node ids/props, scoped to the caller."""
    return _require().search(query, limit, fts=_fts)


def meta(topic: str = "") -> dict:
    """Graph coverage, provenance, and freshness.

    Agent calls this to decide whether to query the graph or work from memory.
    If ``topic`` is given, returns matching node count and ids for that topic.
    """
    scope = _require()
    result = scope.stats(topic)

    if _manifest:
        result["compile"] = {
            "run_id": _manifest.run_id,
            "compiled_at": _manifest.compiled_at or None,
            "merged_count": _manifest.merged_count,
            "quarantined_count": _manifest.quarantined_count,
        }

    pending = _pending_dir()
    if pending and pending.exists():
        from lorekeep.journal import load_journals
        journals = load_journals(pending)
        result["pending"] = sum(1 for j in journals if j.status == "pending")
    else:
        result["pending"] = 0

    return result


def temporal_query(
    mode: Literal["at_time", "history", "changes"],
    params: dict | None = None,
) -> dict:
    """Run one temporal query.

    Params by mode: ``at_time={time}``, ``history={id}``, or
    ``changes={from_time,to_time}``. All results are namespace-scoped.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return {"error": "params must be an object"}
    try:
        if mode == "at_time":
            time = params.get("time", "")
            if not time:
                return {"error": "time is required for mode=at_time"}
            return {"mode": mode, **at_time(time)}
        if mode == "history":
            id = params.get("id", "")
            if not id:
                return {"error": "id is required for mode=history"}
            return {"mode": mode, "items": history(id)}
        if mode == "changes":
            from_time = params.get("from_time", "")
            to_time = params.get("to_time", "")
            if not from_time or not to_time:
                return {
                    "error": "from_time and to_time are required for mode=changes"
                }
            return {"mode": mode, **changes(from_time, to_time)}
    except (TypeError, ValueError) as exc:
        return {"error": f"invalid temporal query: {exc}"}
    return {"error": f"unknown temporal mode: {mode}"}


def context(
    section: Literal["all", "schema", "namespaces", "meta"] = "all",
    topic: str = "",
) -> dict:
    """Return passive Lorekeep context without exposing three separate tools.

    ``section=all`` returns schema, visible namespaces, and graph freshness.
    Use ``topic`` with ``section=meta`` or ``all`` to check topic coverage.
    The same passive data is also exposed as MCP resources.
    """
    if section == "schema":
        return {"schema": schema()}
    if section == "namespaces":
        return {"namespaces": list_namespaces()}
    if section == "meta":
        return {"meta": meta(topic)}
    if section == "all":
        return {
            "schema": schema(),
            "namespaces": list_namespaces(),
            "meta": meta(topic),
        }
    return {"error": f"unknown context section: {section}"}


# --- Write tools (journal-based) -----------------------------------------


def _active_ns() -> tuple[str, ...]:
    allowed = _state.get("allowed_ns", ["public"])
    return tuple(ns for ns in allowed if ns != "public") or ("public",)


def _primary_ns() -> str:
    active = _active_ns()
    return active[0] if active else "public"


def _pending_dir() -> Path | None:
    return _state.get("pending_dir")


def _write_journal(fact_dict: dict, confidence: float, agent: str = "mcp") -> dict:
    pending = _pending_dir()
    if pending is None:
        return {"error": "no pending directory configured"}
    ns = _primary_ns()
    fact_dict["ns"] = list(_active_ns())
    agent = os.environ.get("LOREKEEP_AGENT", agent)
    device = os.environ.get("LOREKEEP_DEVICE", socket.gethostname())
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    entry_id = uuid.uuid4().hex
    entry = JournalEntry(
        fact=fact_dict,
        agent=agent,
        device=device,
        entry_id=entry_id,
        ns=ns,
        confidence=max(0.0, min(1.0, float(confidence))),
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": fact_dict.get("id", ""),
        "status": "pending",
        "ns": ns,
        "proposed_at": now,
        "entry_id": entry_id,
    }


def propose_fact(fact: dict, confidence: float) -> dict:
    """Propose a new node or edge. ns is server-enforced, caller ns is stripped."""
    if not _schema:
        return {"error": "no schema loaded"}
    fact = dict(fact)
    fact_type = fact.get("type", "")
    fact_kind = fact.get("kind", "")
    if fact_kind == "node":
        if not _schema.is_valid_node_type(fact_type):
            return {"error": f"unknown node type: {fact_type}"}
    elif fact_kind == "edge":
        if not _schema.is_valid_edge_type(fact_type):
            return {"error": f"unknown edge type: {fact_type}"}
        scoped = _require()
        from_node = scoped.get_node(fact.get("from", ""))
        to_node = scoped.get_node(fact.get("to", ""))
        if from_node is None or to_node is None:
            return {"error": "edge endpoint not found or out of scope"}
        if not _schema.is_valid_edge_endpoints(
            fact_type, from_node.type, to_node.type,
        ):
            return {
                "error": f"invalid endpoints for {fact_type}: "
                f"{from_node.type}->{to_node.type}"
            }
    else:
        return {"error": f"unknown fact kind: {fact.get('kind')}"}
    fact.pop("ns", None)
    if "src" not in fact:
        fact["src"] = []
    return _write_journal(fact, confidence)


def link_facts(from_id: str, to_id: str, edge_type: str, confidence: float) -> dict:
    """Create an edge between two existing nodes, server-enforced ns."""
    scoped = _require()
    if scoped.get_node(from_id) is None:
        return {"error": f"from node not found or out of scope: {from_id}"}
    if scoped.get_node(to_id) is None:
        return {"error": f"to node not found or out of scope: {to_id}"}
    if not _schema:
        return {"error": "no schema loaded"}
    if not _schema.is_valid_edge_type(edge_type):
        return {"error": f"unknown edge type: {edge_type}"}
    from_node = scoped.get_node(from_id)
    to_node = scoped.get_node(to_id)
    if not _schema.is_valid_edge_endpoints(
        edge_type, from_node.type, to_node.type,
    ):
        return {
            "error": f"invalid endpoints for {edge_type}: "
            f"{from_node.type}->{to_node.type}"
        }
    fact = {
        "kind": "edge",
        "id": "",
        "type": edge_type,
        "from": from_id,
        "to": to_id,
        "ns": [],
        "props": {},
        "src": [],
    }
    return _write_journal(fact, confidence)


def flag_contradiction(fact_a_id: str, fact_b_id: str, description: str) -> dict:
    """Report conflicting facts for curator review (always quarantined)."""
    pending = _pending_dir()
    if pending is None:
        return {"error": "no pending directory configured"}
    ns = _primary_ns()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    flag_fact = {
        "kind": "node",
        "id": f"contradiction:{fact_a_id}:{fact_b_id}",
        "type": "note",
        "ns": list(_active_ns()),
        "props": {
            "title": f"contradiction: {fact_a_id} vs {fact_b_id}",
            "topic": description,
        },
        "src": [],
    }
    entry = JournalEntry(
        fact=flag_fact,
        agent=os.environ.get("LOREKEEP_AGENT", "mcp"),
        device=os.environ.get("LOREKEEP_DEVICE", socket.gethostname()),
        entry_id=uuid.uuid4().hex,
        ns=ns,
        confidence=0.0,
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": flag_fact["id"],
        "status": "pending",
        "note": "Flagged for curator review. Both facts will be quarantined on next resolve.",
    }


def update_fact(id: str, props: dict, confidence: float) -> dict:
    """Propose updated props for an existing node or edge."""
    scoped = _require()
    node = scoped.get_node(id)
    if node is not None:
        fact = node.model_dump(mode="json", by_alias=True)
        fact["props"] = props
        fact.pop("ns", None)
        return _write_journal(fact, confidence)
    edge = scoped.get_edge(id)
    if edge is not None:
        edge_dict = edge.model_dump(mode="json", by_alias=True)
        edge_dict["props"] = props
        edge_dict.pop("ns", None)
        return _write_journal(edge_dict, confidence)
    return {"error": f"fact not found or out of scope: {id}"}


def suggest_improvement(description: str) -> dict:
    """Suggest a non-fact improvement (gap, missing entity) - review only."""
    pending = _pending_dir()
    if pending is None:
        return {"error": "no pending directory configured"}
    ns = _primary_ns()
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    suggestion_fact = {
        "kind": "node",
        "id": f"suggestion:{_primary_ns()}:{now[:19]}",
        "type": "note",
        "ns": list(_active_ns()),
        "props": {
            "title": "improvement suggestion",
            "topic": description,
        },
        "src": [],
    }
    entry = JournalEntry(
        fact=suggestion_fact,
        agent=os.environ.get("LOREKEEP_AGENT", "mcp"),
        device=os.environ.get("LOREKEEP_DEVICE", socket.gethostname()),
        entry_id=uuid.uuid4().hex,
        ns=ns,
        confidence=0.0,
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": suggestion_fact["id"],
        "status": "pending",
        "note": "Suggestion recorded for curator review.",
    }


def propose_change(
    operation: Literal["create", "link", "update"],
    payload: dict,
    confidence: float,
) -> dict:
    """Propose one journal-based graph change; the server enforces namespace.

    Payload by operation: ``create`` is a node/edge fact; ``link`` uses
    ``{from_id,to_id,edge_type,props?}``; ``update`` uses ``{id,props}`` and
    replaces the complete props map. Changes stay pending until resolve.
    """
    if not isinstance(payload, dict):
        return {"error": "payload must be an object"}
    if operation == "create":
        if not payload:
            return {"error": "fact payload is required for operation=create"}
        return propose_fact(payload, confidence)
    if operation == "link":
        from_id = payload.get("from_id", "")
        to_id = payload.get("to_id", "")
        edge_type = payload.get("edge_type", "")
        if not from_id or not to_id or not edge_type:
            return {
                "error": "from_id, to_id, and edge_type are required for operation=link"
            }
        edge_props = payload.get("props") or {}
        if not isinstance(edge_props, dict):
            return {"error": "props must be an object for operation=link"}
        edge = {
            "kind": "edge",
            "id": "",
            "type": edge_type,
            "from": from_id,
            "to": to_id,
            "props": dict(edge_props),
            "src": [],
        }
        return propose_fact(edge, confidence)
    if operation == "update":
        id = payload.get("id", "")
        props = payload.get("props")
        if not id:
            return {"error": "id is required for operation=update"}
        if props is None:
            return {"error": "props is required for operation=update"}
        if not isinstance(props, dict):
            return {"error": "props must be an object for operation=update"}
        return update_fact(id, props, confidence)
    return {"error": f"unknown change operation: {operation}"}


def review_note(
    kind: Literal["contradiction", "improvement"],
    description: str,
    fact_ids: list[str] | None = None,
) -> dict:
    """Record curator-review work without presenting two separate MCP tools.

    ``kind=contradiction`` requires exactly two ``fact_ids``;
    ``kind=improvement`` only needs a description. Notes stay pending.
    """
    if not description.strip():
        return {"error": "description is required"}
    if kind == "contradiction":
        if not fact_ids or len(fact_ids) != 2 or not all(fact_ids):
            return {"error": "exactly two fact_ids are required for contradiction"}
        return flag_contradiction(fact_ids[0], fact_ids[1], description)
    if kind == "improvement":
        return suggest_improvement(description)
    return {"error": f"unknown review kind: {kind}"}


CORE_TOOL_NAMES = (
    "search",
    "get_node",
    "neighbors",
    "temporal_query",
    "context",
    "propose_change",
    "review_note",
)

LEGACY_TOOL_NAMES = (
    "search",
    "get_node",
    "neighbors",
    "at_time",
    "history",
    "changes",
    "list_namespaces",
    "schema",
    "meta",
    "propose_fact",
    "link_facts",
    "flag_contradiction",
    "update_fact",
    "suggest_improvement",
)

FULL_TOOL_NAMES = tuple(dict.fromkeys((*CORE_TOOL_NAMES, *LEGACY_TOOL_NAMES)))

_TOOL_REGISTRY = {
    fn.__name__: fn
    for fn in (
        search,
        get_node,
        neighbors,
        temporal_query,
        context,
        propose_change,
        review_note,
        at_time,
        history,
        changes,
        list_namespaces,
        schema,
        meta,
        propose_fact,
        link_facts,
        flag_contradiction,
        update_fact,
        suggest_improvement,
    )
}


def normalize_mcp_profile(profile: str | None = None) -> str:
    """Resolve and validate a profile name from an argument or environment."""
    selected = (profile or os.environ.get("LOREKEEP_MCP_PROFILE") or DEFAULT_MCP_PROFILE)
    selected = selected.strip().lower()
    if selected not in MCP_PROFILES:
        raise ValueError(
            f"unknown MCP profile: {selected!r} (choose {'|'.join(MCP_PROFILES)})"
        )
    return selected


def _resource_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _schema_resource() -> str:
    """Current ontology schema as JSON."""
    return _resource_json(schema())


def _namespaces_resource() -> str:
    """Namespaces visible to the connected agent as JSON."""
    return _resource_json(list_namespaces())


def _status_resource() -> str:
    """Graph coverage, provenance, freshness, and pending count as JSON."""
    return _resource_json(meta())


_RESOURCE_SPECS = (
    ("lorekeep://schema", "schema", "Ontology node and edge types", _schema_resource),
    (
        "lorekeep://namespaces",
        "namespaces",
        "Namespaces visible to this agent",
        _namespaces_resource,
    ),
    (
        "lorekeep://status",
        "status",
        "Graph coverage, provenance, freshness, and pending count",
        _status_resource,
    ),
)


def create_mcp(profile: str | None = None) -> FastMCP:
    """Build an isolated FastMCP server for ``core`` or ``full`` exposure."""
    selected = normalize_mcp_profile(profile)
    tool_names = CORE_TOOL_NAMES if selected == "core" else FULL_TOOL_NAMES
    server = FastMCP("lorekeep")
    for name in tool_names:
        server.add_tool(_TOOL_REGISTRY[name], name=name)
    for uri, name, description, reader in _RESOURCE_SPECS:
        server.resource(
            uri,
            name=name,
            description=description,
            mime_type="application/json",
        )(reader)
    return server


# Importers historically use ``from lorekeep.mcp_server import mcp``.  Keep a
# module-level server while making its default surface deliberately compact.
mcp = create_mcp(DEFAULT_MCP_PROFILE)


if __name__ == "__main__":
    create_mcp().run()
