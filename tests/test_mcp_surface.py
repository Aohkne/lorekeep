"""Compact MCP surface registration and routing tests."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

import lorekeep.mcp_server as ms


def _tool_map() -> dict:
    return {tool.name: tool for tool in asyncio.run(ms.mcp.list_tools())}


def _setup(tmp_path: Path, fixtures: Path, *, pending: bool = False) -> Path | None:
    graph = tmp_path / "graph"
    graph.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", graph / "facts.jsonl")
    pending_dir = tmp_path / "pending" if pending else None
    if pending_dir:
        pending_dir.mkdir()
    ms.configure(
        graph_dir=graph,
        allowed_ns=["backend"],
        schema_path=fixtures / "schema.json",
        pending_dir=pending_dir,
    )
    return pending_dir


def _entries(pending: Path) -> list[dict]:
    result = []
    for path in sorted(pending.rglob("journal.jsonl")):
        result.extend(json.loads(line) for line in path.read_text().splitlines())
    return result


def test_server_exposes_exactly_eight_composable_tools():
    tools = _tool_map()

    assert tuple(tools) == (
        "search", "get_node", "neighbors", "temporal_query", "context",
        "propose_change", "merge_entities", "review_note",
    )
    assert len(tools) == 8
    assert set(tools["temporal_query"].inputSchema["properties"]["mode"]["enum"]) == {
        "at_time", "history", "changes",
    }
    assert set(tools["propose_change"].inputSchema["properties"]["operation"]["enum"]) == {
        "create", "link", "update",
    }


def test_server_publishes_passive_context_as_resources():
    resources = asyncio.run(ms.mcp.list_resources())

    assert {str(resource.uri) for resource in resources} == {
        "lorekeep://schema",
        "lorekeep://namespaces",
        "lorekeep://status",
    }
    assert all(resource.mimeType == "application/json" for resource in resources)


def test_passive_resources_return_scoped_json(tmp_path: Path, fixtures: Path):
    _setup(tmp_path, fixtures)

    schema_payload = asyncio.run(ms.mcp.read_resource("lorekeep://schema"))[0]
    namespaces_payload = asyncio.run(
        ms.mcp.read_resource("lorekeep://namespaces")
    )[0]
    status_payload = asyncio.run(ms.mcp.read_resource("lorekeep://status"))[0]

    assert "service" in json.loads(schema_payload.content)["node_types"]
    assert json.loads(namespaces_payload.content) == ["backend", "public"]
    assert json.loads(status_payload.content)["nodes"] >= 1


def test_temporal_query_routes_all_modes(tmp_path: Path, fixtures: Path):
    _setup(tmp_path, fixtures)

    snapshot = ms.temporal_query("at_time", {"time": "2025-02-28"})
    assert snapshot["mode"] == "at_time"
    assert "depends_on" in {edge["type"] for edge in snapshot["edges"]}

    history = ms.temporal_query("history", {"id": "svc:payments-api"})
    assert history["mode"] == "history"
    assert history["items"][0]["kind"] == "node"

    changes = ms.temporal_query(
        "changes", {"from_time": "2024-01-01", "to_time": "2025-04-01"},
    )
    assert changes["mode"] == "changes"
    assert "depends_on" in {edge["type"] for edge in changes["began"]}


@pytest.mark.parametrize(
    ("mode", "kwargs", "message"),
    [
        ("at_time", {}, "time is required"),
        ("history", {}, "id is required"),
        ("changes", {"params": {"from_time": "2024-01-01"}}, "to_time"),
        ("at_time", {"params": {"time": "not-a-date"}}, "invalid temporal query"),
        ("at_time", {"params": "bad"}, "params must be an object"),
        ("unknown", {}, "unknown temporal mode"),
    ],
)
def test_temporal_query_reports_actionable_errors(
    tmp_path: Path, fixtures: Path, mode: str, kwargs: dict, message: str,
):
    _setup(tmp_path, fixtures)
    result = ms.temporal_query(mode, **kwargs)
    assert message in result["error"]


def test_context_combines_schema_scope_and_freshness(tmp_path: Path, fixtures: Path):
    _setup(tmp_path, fixtures)

    result = ms.context(topic="payments")

    assert "service" in result["schema"]["node_types"]
    assert result["namespaces"] == ["backend", "public"]
    assert result["status"]["coverage"]["matching_nodes"] >= 1
    assert "error" in ms.context("unknown")


def test_propose_change_routes_create_link_and_update(
    tmp_path: Path, fixtures: Path,
):
    pending = _setup(tmp_path, fixtures, pending=True)
    fact = {
        "kind": "node",
        "id": "svc:new",
        "type": "service",
        "props": {"name": "new"},
    }

    assert ms.propose_change("create", fact, 0.9)["accepted"] is True
    assert ms.propose_change(
        "link",
        {
            "from_id": "svc:payments-api",
            "to_id": "svc:auth",
            "edge_type": "depends_on",
            "props": {"reason": "runtime"},
        },
        0.85,
    )["accepted"] is True
    assert ms.propose_change(
        "update", {"id": "svc:payments-api", "props": {"lang": "rust"}}, 0.8,
    )["accepted"] is True

    entries = _entries(pending)
    assert [entry["fact"]["kind"] for entry in entries] == ["node", "edge", "node"]
    assert entries[1]["fact"]["props"] == {"reason": "runtime"}
    assert entries[2]["fact"]["props"] == {"lang": "rust"}
    assert "ns" not in fact, "server routing must not mutate the caller payload"


@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        ("create", {}, "fact payload is required"),
        ("link", {"from_id": "a"}, "to_id"),
        (
            "link",
            {"from_id": "a", "to_id": "b", "edge_type": "x", "props": "bad"},
            "props must be an object",
        ),
        ("update", {}, "id is required"),
        ("update", {"id": "a"}, "props is required"),
        ("update", {"id": "a", "props": "bad"}, "props must be an object"),
        ("delete", {}, "unknown change operation"),
    ],
)
def test_propose_change_rejects_incomplete_payloads(
    tmp_path: Path,
    fixtures: Path,
    operation: str,
    payload: dict,
    message: str,
):
    _setup(tmp_path, fixtures, pending=True)
    assert message in ms.propose_change(operation, payload, 0.8)["error"]


def test_review_note_routes_both_review_kinds(tmp_path: Path, fixtures: Path):
    pending = _setup(tmp_path, fixtures, pending=True)

    contradiction = ms.review_note(
        "contradiction",
        "incompatible runtime assumptions",
        fact_ids=["svc:payments-api", "svc:auth"],
    )
    improvement = ms.review_note("improvement", "document the auth flow")

    assert contradiction["accepted"] is True
    assert improvement["accepted"] is True
    assert [entry["confidence"] for entry in _entries(pending)] == [0.0, 0.0]


def test_review_note_validates_inputs(tmp_path: Path, fixtures: Path):
    _setup(tmp_path, fixtures, pending=True)

    assert "description" in ms.review_note("improvement", "  ")["error"]
    assert "two fact_ids" in ms.review_note("contradiction", "conflict")["error"]
    assert "unknown review kind" in ms.review_note("other", "note")["error"]
