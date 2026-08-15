"""Unit tests for compact journal-based MCP writes."""
import json
import shutil
import tempfile
from pathlib import Path

import lorekeep.mcp_server as ms
import pytest


def _setup(fixtures: Path, allowed, with_pending=True, write_ns="backend"):
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    pending = d / "pending" if with_pending else None
    if with_pending:
        pending.mkdir()
    ms.configure(
        graph_dir=d,
        allowed_ns=allowed,
        schema_path=fixtures / "schema.json",
        pending_dir=pending,
        write_ns=write_ns,
    )
    return d, pending


def _journal_entries(pending: Path) -> list[dict]:
    entries = []
    for jf in sorted(pending.rglob("journal.jsonl")):
        for line in jf.read_text().splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _create(fact: dict, confidence: float) -> dict:
    return ms.propose_change("create", fact, confidence)


def _link(from_id: str, to_id: str, edge_type: str, confidence: float) -> dict:
    return ms.propose_change(
        "link",
        {"from_id": from_id, "to_id": to_id, "edge_type": edge_type},
        confidence,
    )


def _update(id: str, props: dict, confidence: float) -> dict:
    return ms.propose_change("update", {"id": id, "props": props}, confidence)


def _contradiction(fact_a: str, fact_b: str, description: str) -> dict:
    return ms.review_note("contradiction", description, [fact_a, fact_b])


def _improvement(description: str) -> dict:
    return ms.review_note("improvement", description)


# ── create operation ─────────────────────────────────────────────────────


def test_create_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    fact = {
        "kind": "node",
        "id": "svc:new-service",
        "type": "service",
        "props": {"name": "new-service"},
    }
    r = _create(fact, confidence=0.9)
    assert r["accepted"] is True
    assert r["status"] == "pending"

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["id"] == "svc:new-service"
    assert entries[0]["status"] == "pending"
    assert entries[0]["confidence"] == 0.9


def test_create_rejects_invalid_node_type(fixtures: Path):
    _setup(fixtures, ["backend"])
    fact = {"kind": "node", "id": "x", "type": "nonexistent", "props": {}}
    r = _create(fact, confidence=0.9)
    assert "error" in r


def test_create_strips_caller_ns(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    fact = {
        "kind": "node",
        "id": "svc:stripped",
        "type": "service",
        "ns": ["evil"],
        "props": {},
    }
    _create(fact, confidence=0.9)
    entries = _journal_entries(pending)
    assert "evil" not in entries[0]["fact"].get("ns", [])


def test_create_without_pending_dir(fixtures: Path):
    _setup(fixtures, ["backend"], with_pending=False)
    fact = {"kind": "node", "id": "x", "type": "service", "props": {}}
    r = _create(fact, confidence=0.9)
    assert "error" in r


def test_create_wildcard_read_scope_writes_only_concrete_namespace(fixtures: Path):
    _, pending = _setup(fixtures, ["*"], write_ns="me")
    fact = {
        "kind": "node", "id": "svc:wildcard-safe", "type": "service",
        "props": {"name": "wildcard-safe"},
    }

    result = _create(fact, confidence=0.9)

    assert result["ns"] == "me"
    entries = _journal_entries(pending)
    assert entries[0]["fact"]["ns"] == ["me"]
    assert (pending / "me" / "journal.jsonl").is_file()
    assert not (pending / "*" / "journal.jsonl").exists()


def test_create_without_concrete_write_namespace_returns_error(fixtures: Path):
    _setup(fixtures, ["*"], write_ns=None)
    fact = {"kind": "node", "id": "svc:no-owner", "type": "service", "props": {}}
    result = _create(fact, confidence=0.9)
    assert result == {"error": "no concrete write namespace configured"}


@pytest.mark.parametrize("write_ns", ["*", "", "me,backend"])
def test_configure_rejects_non_concrete_write_namespace(
    fixtures: Path, tmp_path: Path, write_ns: str,
):
    graph = tmp_path / "graph"
    graph.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", graph / "facts.jsonl")
    with pytest.raises(ValueError, match="one concrete namespace"):
        ms.configure(graph_dir=graph, allowed_ns=["*"], write_ns=write_ns)


# ── link operation ───────────────────────────────────────────────────────


def test_link_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = _link("svc:payments-api", "svc:auth", "depends_on", confidence=0.85)
    assert r["accepted"] is True

    entries = _journal_entries(pending)
    assert len(entries) == 1
    fact = entries[0]["fact"]
    assert fact["kind"] == "edge"
    assert fact["from"] == "svc:payments-api"
    assert fact["to"] == "svc:auth"
    assert fact["type"] == "depends_on"


def test_link_rejects_unknown_from(fixtures: Path):
    _setup(fixtures, ["backend"])
    r = _link("svc:nonexistent", "svc:auth", "depends_on", confidence=0.8)
    assert "error" in r


def test_link_rejects_unknown_edge_type(fixtures: Path):
    _setup(fixtures, ["backend"])
    r = _link("svc:payments-api", "svc:auth", "bogus_type", confidence=0.8)
    assert "error" in r


def test_link_rejects_invalid_endpoint_types(fixtures: Path):
    _setup(fixtures, ["backend"])
    result = _link(
        "team:backend", "svc:auth", "depends_on", confidence=0.8,
    )
    assert "invalid endpoints" in result["error"]


# ── contradiction review ─────────────────────────────────────────────────


def test_contradiction_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = _contradiction("svc:payments-api", "svc:auth", "mutually exclusive configs")
    assert r["accepted"] is True
    assert "contradiction" in r["id"]

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["confidence"] == 0.0


def test_contradiction_without_pending_dir(fixtures: Path):
    _setup(fixtures, ["backend"], with_pending=False)
    r = _contradiction("svc:payments-api", "svc:auth", "test")
    assert "error" in r


# ── update operation ─────────────────────────────────────────────────────


def test_update_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = _update("svc:payments-api", {"lang": "rust"}, confidence=0.8)
    assert r["accepted"] is True

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["props"]["lang"] == "rust"


def test_update_rejects_unknown_id(fixtures: Path):
    _setup(fixtures, ["backend"])
    r = _update("svc:nonexistent", {"lang": "rust"}, confidence=0.8)
    assert "error" in r


def test_update_rejects_hidden_id(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    hidden = {
        "kind": "node", "id": "svc:secret", "type": "service",
        "ns": ["private"], "valid_from": None, "valid_to": None,
        "props": {"name": "secret"}, "src": [],
    }
    with (d / "facts.jsonl").open("a") as f:
        f.write(json.dumps(hidden) + "\n")
    ms.configure(
        graph_dir=d,
        allowed_ns=["backend"],
        schema_path=fixtures / "schema.json",
        pending_dir=pending,
        write_ns="backend",
    )

    result = _update("svc:secret", {"lang": "rust"}, confidence=0.8)

    assert "error" in result
    assert _journal_entries(pending) == []


# ── improvement review ───────────────────────────────────────────────────


def test_improvement_writes_journal(fixtures: Path):
    d, pending = _setup(fixtures, ["backend"])
    r = _improvement("Add documentation for auth flow")
    assert r["accepted"] is True
    assert "suggestion" in r["id"]

    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["type"] == "note"


def test_improvement_wildcard_read_scope_uses_concrete_write_namespace(fixtures: Path):
    _, pending = _setup(fixtures, ["*"], write_ns="me")
    result = _improvement("Keep wildcard out of journal ownership")
    assert result["ns"] == "me"
    entry = _journal_entries(pending)[0]
    assert entry["fact"]["ns"] == ["me"]
    assert entry["fact"]["id"].startswith("suggestion:me:")


def test_improvement_without_concrete_write_namespace_returns_error(fixtures: Path):
    _setup(fixtures, ["*"], write_ns=None)
    result = _improvement("No write owner")
    assert result == {"error": "no concrete write namespace configured"}


def test_improvement_without_pending_dir(fixtures: Path):
    _setup(fixtures, ["backend"], with_pending=False)
    r = _improvement("test suggestion")
    assert "error" in r


# ── create edge validation ───────────────────────────────────────────────


def test_create_no_schema(fixtures: Path):
    """Create returns an error when no schema is loaded."""
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=["backend"], schema_path=None,
                 pending_dir=d / "pending")
    fact = {"kind": "node", "id": "x", "type": "service", "props": {}}
    r = _create(fact, confidence=0.9)
    assert "error" in r and "schema" in r["error"]


def test_create_edge_valid(fixtures: Path):
    """A valid edge proposal passes all validation checks."""
    d, pending = _setup(fixtures, ["backend"])
    fact = {
        "kind": "edge", "id": "e_test_edge", "type": "depends_on",
        "from": "svc:payments-api", "to": "svc:auth", "props": {},
    }
    r = _create(fact, confidence=0.85)
    assert r["accepted"] is True


def test_create_edge_unknown_type(fixtures: Path):
    _setup(fixtures, ["backend"])
    fact = {
        "kind": "edge", "id": "e_bad", "type": "bogus_edge",
        "from": "svc:payments-api", "to": "svc:auth", "props": {},
    }
    r = _create(fact, confidence=0.8)
    assert "error" in r and "unknown edge type" in r["error"]


def test_create_edge_endpoint_not_found(fixtures: Path):
    _setup(fixtures, ["backend"])
    fact = {
        "kind": "edge", "id": "e_orphan", "type": "depends_on",
        "from": "svc:nonexistent", "to": "svc:auth", "props": {},
    }
    r = _create(fact, confidence=0.8)
    assert "error" in r and "from node" in r["error"]


def test_create_edge_invalid_endpoints(fixtures: Path):
    """depends_on requires service→service, not team→service."""
    _setup(fixtures, ["backend"])
    fact = {
        "kind": "edge", "id": "e_wrong", "type": "depends_on",
        "from": "team:backend", "to": "svc:auth", "props": {},
    }
    r = _create(fact, confidence=0.8)
    assert "error" in r and "invalid endpoints" in r["error"]


def test_create_unknown_kind(fixtures: Path):
    _setup(fixtures, ["backend"])
    fact = {"kind": "bogus", "id": "x", "type": "service", "props": {}}
    r = _create(fact, confidence=0.8)
    assert "error" in r and "unknown fact kind" in r["error"]


# ── link edge cases ──────────────────────────────────────────────────────


def test_link_unknown_to(fixtures: Path):
    """Link rejects when the 'to' node does not exist."""
    _setup(fixtures, ["backend"])
    r = _link("svc:payments-api", "svc:nonexistent", "depends_on",
              confidence=0.8)
    assert "error" in r and "to node" in r["error"]


def test_link_no_schema(fixtures: Path):
    """Link returns an error when no schema is loaded."""
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=["backend"], schema_path=None,
                 pending_dir=d / "pending")
    r = _link("svc:payments-api", "svc:auth", "depends_on",
              confidence=0.8)
    assert "error" in r and "schema" in r["error"]


# ── update edge ──────────────────────────────────────────────────────────


def test_update_edge(fixtures: Path):
    """Update can replace an edge's props, not just a node's."""
    d, pending = _setup(fixtures, ["backend"])
    # e_dep_1 is an existing edge in the payments fixture
    r = _update("e_dep_1", {"weight": "critical"}, confidence=0.9)
    assert r["accepted"] is True
    entries = _journal_entries(pending)
    assert len(entries) == 1
    assert entries[0]["fact"]["props"]["weight"] == "critical"
