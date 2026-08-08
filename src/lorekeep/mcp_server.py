"""FastMCP server exposing a compact, namespace-scoped temporal graph.

The seven tool functions remain directly callable for tests and diagnostics.
Writes append to pending/<ns>/journal.jsonl and enter the graph on resolve.
"""
from __future__ import annotations

import atexit
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
from lorekeep.store.fts import FTSIndex
from lorekeep.store.graph import GraphStore, parse_date

log = logging.getLogger("lorekeep.mcp")
mcp = FastMCP("lorekeep")

_state: dict = {}
_scope: ScopedGraph | None = None
_schema: Schema | None = None
_manifest: Manifest | None = None
_fts: FTSIndex | None = None


def _close_fts() -> None:
    """Close the FTS sqlite connection if open (atexit + pre-rebuild)."""
    global _fts
    if _fts is not None:
        try:
            _fts.close()
        except Exception:
            pass
        _fts = None


atexit.register(_close_fts)


def configure(graph_dir, allowed_ns, schema_path=None, fts_path=None, pending_dir=None) -> None:
    """Set the graph location and permission scope, then load the store."""
    _state.update(
        graph_dir=Path(graph_dir),
        allowed_ns=list(allowed_ns),
        schema_path=Path(schema_path) if schema_path else None,
        pending_dir=Path(pending_dir) if pending_dir else None,
        fts_path=Path(fts_path) if fts_path else Path(graph_dir) / "fts.sqlite",
    )
    log.info(
        "configuring graph namespace_count=%s", len(_state["allowed_ns"]),
        extra={"event": "mcp.configure"},
    )
    _rebuild()


def _rebuild() -> None:
    """Reload graph, schema, manifest, and full-text index from disk."""
    global _scope, _schema, _manifest, _fts
    facts = _state["graph_dir"] / "facts.jsonl"
    if not facts.exists():
        raise FileNotFoundError(
            f"facts.jsonl not found at {facts}. "
            "Run 'lorekeep compile' first to build the knowledge graph."
        )
    store = GraphStore.from_jsonl(facts)
    schema_path = _state.get("schema_path")
    _schema = load_schema(schema_path) if schema_path else None
    _scope = ScopedGraph(store, _state["allowed_ns"])
    manifest_path = _state["graph_dir"] / "manifest.json"
    _manifest = (
        Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else None
    )
    _close_fts()
    try:
        _fts = FTSIndex(_state["fts_path"])
        _fts.build(store.all_nodes())
    except Exception as exc:
        log.warning(
            "FTS unavailable; falling back to graph search error_type=%s",
            type(exc).__name__, extra={"event": "mcp.fts_fallback"},
        )
        _fts = None
    _state["facts_mtime"] = facts.stat().st_mtime
    log.info(
        "graph loaded node_count=%s edge_count=%s fts=%s",
        len(store.node_ids()), len(store.all_edges()), _fts is not None,
        extra={"event": "mcp.graph_loaded"},
    )


def _require() -> ScopedGraph:
    """Return the scoped graph, reloading when facts.jsonl changes."""
    if not _state:
        raise RuntimeError("mcp_server not configured; call configure() first")
    facts = _state["graph_dir"] / "facts.jsonl"
    mtime = facts.stat().st_mtime if facts.exists() else 0
    if _scope is None or mtime != _state.get("facts_mtime"):
        log.info("facts changed; reloading graph", extra={"event": "mcp.reload"})
        _rebuild()
    return _scope


def _graph_payload(nodes, edges) -> dict:
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in nodes],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in edges],
    }


def _schema_payload() -> dict:
    if _schema is None:
        return {"error": "no schema loaded"}
    return _schema.model_dump(mode="json", by_alias=True)


def _status(topic: str = "") -> dict:
    result = _require().stats(topic)
    if _manifest:
        result["compile"] = {
            "run_id": _manifest.run_id,
            "compiled_at": _manifest.compiled_at or None,
            "merged_count": _manifest.merged_count,
            "quarantined_count": _manifest.quarantined_count,
        }
    pending = _state.get("pending_dir")
    if pending and pending.exists():
        from lorekeep.journal import load_journals
        result["pending"] = sum(
            entry.status == "pending" for entry in load_journals(pending)
        )
    else:
        result["pending"] = 0
    return result


@mcp.tool()
def search(query: str, limit: int = 10) -> list:
    """Text search over node ids and properties, scoped to the caller."""
    return _require().search(query, limit, fts=_fts)


@mcp.tool()
def get_node(id: str) -> dict:
    """Return a node by id, or an error when absent or out of scope."""
    node = _require().get_node(id)
    if node is None:
        return {"error": "not found or out of scope"}
    return node.model_dump(mode="json", by_alias=True)


@mcp.tool()
def neighbors(id: str, edge_type: str = "", depth: int = 1) -> dict:
    """Traverse scoped neighbors in both directions, up to five hops."""
    result = _require().neighbors(
        id, edge_type or None, max(1, min(int(depth), 5)),
    )
    return _graph_payload(result["nodes"], result["edges"])


@mcp.tool()
def temporal_query(
    mode: Literal["at_time", "history", "changes"],
    params: dict | None = None,
) -> dict:
    """Query a snapshot, entity history, or changes between two dates.

    Params: ``at_time={time}``, ``history={id}``, or
    ``changes={from_time,to_time}``. All results are namespace-scoped.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return {"error": "params must be an object"}
    scoped = _require()
    try:
        if mode == "at_time":
            if not (time := params.get("time")):
                return {"error": "time is required for mode=at_time"}
            nodes, edges = scoped.snapshot(parse_date(time))
            return {"mode": mode, **_graph_payload(nodes, edges)}
        if mode == "history":
            if not (id := params.get("id")):
                return {"error": "id is required for mode=history"}
            return {"mode": mode, "items": scoped.history(id)}
        if mode == "changes":
            from_time = params.get("from_time")
            to_time = params.get("to_time")
            if not from_time or not to_time:
                return {
                    "error": "from_time and to_time are required for mode=changes"
                }
            return {
                "mode": mode,
                **scoped.changes(parse_date(from_time), parse_date(to_time)),
            }
    except (TypeError, ValueError) as exc:
        return {"error": f"invalid temporal query: {exc}"}
    return {"error": f"unknown temporal mode: {mode}"}


@mcp.tool()
def context(
    section: Literal["all", "schema", "namespaces", "status"] = "all",
    topic: str = "",
) -> dict:
    """Return ontology, visible namespaces, and graph status.

    Select one section or use ``all``. A topic narrows status coverage.
    The same data is available as passive MCP resources.
    """
    values = {
        "schema": _schema_payload,
        "namespaces": lambda: _require().list_namespaces(),
        "status": lambda: _status(topic),
    }
    if section == "all":
        return {name: read() for name, read in values.items()}
    if section not in values:
        return {"error": f"unknown context section: {section}"}
    return {section: values[section]()}


def _active_ns() -> tuple[str, ...]:
    allowed = _state.get("allowed_ns", ["public"])
    return tuple(ns for ns in allowed if ns != "public") or ("public",)


def _write_journal(fact: dict, confidence: float) -> dict:
    pending = _state.get("pending_dir")
    if pending is None:
        return {"error": "no pending directory configured"}
    fact = dict(fact)
    active_ns = _active_ns()
    fact["ns"] = list(active_ns)
    ns = active_ns[0]
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    entry = JournalEntry(
        fact=fact,
        agent=os.environ.get("LOREKEEP_AGENT", "mcp"),
        device=os.environ.get("LOREKEEP_DEVICE", socket.gethostname()),
        entry_id=uuid.uuid4().hex,
        ns=ns,
        confidence=max(0.0, min(1.0, float(confidence))),
        proposed_at=now,
        status="pending",
    )
    append_journal(pending, entry, ns)
    return {
        "accepted": True,
        "id": fact.get("id", ""),
        "status": "pending",
        "ns": ns,
        "proposed_at": now,
        "entry_id": entry.entry_id,
    }


def _create_fact(fact: dict, confidence: float) -> dict:
    if _schema is None:
        return {"error": "no schema loaded"}
    fact = dict(fact)
    kind, fact_type = fact.get("kind"), fact.get("type", "")
    if kind == "node":
        if not _schema.is_valid_node_type(fact_type):
            return {"error": f"unknown node type: {fact_type}"}
    elif kind == "edge":
        if not _schema.is_valid_edge_type(fact_type):
            return {"error": f"unknown edge type: {fact_type}"}
        scoped = _require()
        from_node = scoped.get_node(fact.get("from", ""))
        to_node = scoped.get_node(fact.get("to", ""))
        if from_node is None:
            return {"error": "from node not found or out of scope"}
        if to_node is None:
            return {"error": "to node not found or out of scope"}
        if not _schema.is_valid_edge_endpoints(
            fact_type, from_node.type, to_node.type,
        ):
            return {
                "error": f"invalid endpoints for {fact_type}: "
                f"{from_node.type}->{to_node.type}"
            }
    else:
        return {"error": f"unknown fact kind: {kind}"}
    fact.pop("ns", None)
    fact.setdefault("src", [])
    return _write_journal(fact, confidence)


@mcp.tool()
def propose_change(
    operation: Literal["create", "link", "update"],
    payload: dict,
    confidence: float,
) -> dict:
    """Propose a create, link, or update through the pending journal.

    ``create`` accepts a node/edge fact; ``link`` accepts
    ``{from_id,to_id,edge_type,props?}``; ``update`` accepts ``{id,props}``.
    Namespace is server-enforced and updates replace the complete props map.
    """
    if not isinstance(payload, dict):
        return {"error": "payload must be an object"}
    if operation == "create":
        if not payload:
            return {"error": "fact payload is required for operation=create"}
        return _create_fact(payload, confidence)
    if operation == "link":
        required = ("from_id", "to_id", "edge_type")
        if missing := [key for key in required if not payload.get(key)]:
            return {"error": f"{', '.join(missing)} required for operation=link"}
        props = payload.get("props", {})
        if not isinstance(props, dict):
            return {"error": "props must be an object for operation=link"}
        return _create_fact(
            {
                "kind": "edge",
                "id": "",
                "type": payload["edge_type"],
                "from": payload["from_id"],
                "to": payload["to_id"],
                "props": props,
            },
            confidence,
        )
    if operation == "update":
        if not (id := payload.get("id")):
            return {"error": "id is required for operation=update"}
        if "props" not in payload:
            return {"error": "props is required for operation=update"}
        if not isinstance(payload["props"], dict):
            return {"error": "props must be an object for operation=update"}
        scoped = _require()
        current = scoped.get_node(id) or scoped.get_edge(id)
        if current is None:
            return {"error": f"fact not found or out of scope: {id}"}
        fact = current.model_dump(mode="json", by_alias=True)
        fact["props"] = payload["props"]
        fact.pop("ns", None)
        return _write_journal(fact, confidence)
    return {"error": f"unknown change operation: {operation}"}


@mcp.tool()
def review_note(
    kind: Literal["contradiction", "improvement"],
    description: str,
    fact_ids: list[str] | None = None,
) -> dict:
    """Record a contradiction or improvement for curator review."""
    if not description.strip():
        return {"error": "description is required"}
    if kind == "contradiction":
        if not fact_ids or len(fact_ids) != 2 or not all(fact_ids):
            return {"error": "exactly two fact_ids are required for contradiction"}
        id = f"contradiction:{fact_ids[0]}:{fact_ids[1]}"
        title = f"contradiction: {fact_ids[0]} vs {fact_ids[1]}"
        note = "Flagged for curator review."
    elif kind == "improvement":
        id = f"suggestion:{_active_ns()[0]}:{uuid.uuid4().hex}"
        title = "improvement suggestion"
        note = "Suggestion recorded for curator review."
    else:
        return {"error": f"unknown review kind: {kind}"}
    result = _write_journal(
        {
            "kind": "node",
            "id": id,
            "type": "note",
            "props": {"title": title, "topic": description},
            "src": [],
        },
        confidence=0.0,
    )
    if "error" not in result:
        result["note"] = note
    return result


def _resource_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@mcp.resource(
    "lorekeep://schema", name="schema",
    description="Ontology node and edge types", mime_type="application/json",
)
def _schema_resource() -> str:
    return _resource_json(_schema_payload())


@mcp.resource(
    "lorekeep://namespaces", name="namespaces",
    description="Namespaces visible to this agent", mime_type="application/json",
)
def _namespaces_resource() -> str:
    return _resource_json(_require().list_namespaces())


@mcp.resource(
    "lorekeep://status", name="status",
    description="Graph coverage, provenance, freshness, and pending count",
    mime_type="application/json",
)
def _status_resource() -> str:
    return _resource_json(_status())


if __name__ == "__main__":
    mcp.run()
