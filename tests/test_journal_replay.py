import json
from concurrent.futures import ThreadPoolExecutor

from lorekeep.cli import _do_auto_resolve
from lorekeep.defaults import DEFAULT_SCHEMA
from lorekeep.facts_io import read_facts
from lorekeep.journal import append_journal
from lorekeep.models import JournalEntry, Node


def test_post_compile_replays_accepted_agent_facts(tmp_path):
    out = tmp_path / "graph"
    out.mkdir()
    raw_fact = {
        "kind": "node", "id": "svc:raw", "type": "service",
        "ns": ["backend"], "valid_from": None, "valid_to": None,
        "props": {"name": "raw"}, "src": ["raw/backend/x.md:1"],
    }
    (out / "facts.jsonl").write_text(json.dumps(raw_fact) + "\n")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(DEFAULT_SCHEMA))
    pending = tmp_path / "pending"
    agent_fact = {
        "kind": "node", "id": "svc:agent", "type": "service",
        "ns": ["backend"], "valid_from": None, "valid_to": None,
        "props": {"name": "agent"}, "src": [],
    }
    append_journal(
        pending,
        JournalEntry(
            entry_id="accepted-1",
            fact=agent_fact,
            agent="codex",
            device="laptop",
            ns="backend",
            confidence=0.9,
            proposed_at="2026-07-29T00:00:00Z",
            status="merged",
        ),
        "backend",
    )

    changed = _do_auto_resolve(
        out, pending, schema_path=schema, replay_accepted=True,
    )

    assert changed is True
    nodes = [fact for fact in read_facts(out / "facts.jsonl") if isinstance(fact, Node)]
    assert {node.id for node in nodes} == {"svc:raw", "svc:agent"}


def test_concurrent_resolve_transactions_do_not_drop_facts(tmp_path):
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text("")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(DEFAULT_SCHEMA))
    pending = tmp_path / "pending"
    for index in range(2):
        append_journal(
            pending,
            JournalEntry(
                entry_id=f"pending-{index}",
                fact={
                    "kind": "node", "id": f"svc:{index}", "type": "service",
                    "ns": ["backend"], "valid_from": None, "valid_to": None,
                    "props": {}, "src": [],
                },
                agent="codex", device="laptop", ns="backend",
                confidence=0.9, proposed_at=f"2026-07-29T00:00:0{index}Z",
                status="pending",
            ),
            "backend",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(
            lambda _: _do_auto_resolve(out, pending, schema_path=schema),
            range(2),
        ))

    nodes = [fact for fact in read_facts(out / "facts.jsonl") if isinstance(fact, Node)]
    assert {node.id for node in nodes} == {"svc:0", "svc:1"}
