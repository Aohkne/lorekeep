from datetime import date
import json
from laputa.models import DocChunk, Schema
from laputa.compile.extract import build_prompt, parse_response, SYSTEM_PROMPT


SCHEMA = Schema.load({
    "version": 1,
    "node_types": {"service": {"props": {"name": "string", "lang": "string"}},
                   "team": {"props": {"name": "string"}},
                   "decision": {"props": {"title": "string"}}},
    "edge_types": {"depends_on": {"from": "service", "to": "service"},
                   "decided_by": {"from": "decision", "to": "team"}},
})


def make_chunk(text="x"):
    return DocChunk(path="raw/teams/backend/a.md", start_line=3, end_line=3,
                    text=text, namespace="teams/backend")


def test_prompt_contains_schema_and_chunk():
    c = make_chunk("The payments-api is a Go service.")
    p = build_prompt(c, SCHEMA)
    assert "service" in p and "depends_on" in p
    assert "payments-api" in p
    assert "raw/teams/backend/a.md:3" in p


def test_parse_response_maps_nodes_and_edges():
    c = make_chunk()
    raw = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth"},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
        ],
        "aliases": {"payments-api": ["payments-api"]},
    })
    nodes, edges, aliases = parse_response(raw, c)
    assert len(nodes) == 2
    assert nodes[0].id == "svc:payments-api"
    assert nodes[0].ns == ("teams/backend",)
    assert nodes[0].src == ("raw/teams/backend/a.md:3",)
    assert nodes[0].valid_from == date(2024, 1, 15)
    assert len(edges) == 1
    assert edges[0].from_ == "svc:payments-api"
    assert edges[0].valid_to == date(2025, 3, 1)
    assert aliases == {"payments-api": ["payments-api"]}


def test_parse_response_skips_invalid_node_type():
    c = make_chunk()
    raw = json.dumps({"nodes": [{"id": "x", "type": "bogus", "name": "x"}], "edges": []})
    nodes, edges, aliases = parse_response(raw, c, schema=SCHEMA)
    assert nodes == []
