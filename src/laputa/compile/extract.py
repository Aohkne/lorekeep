"""Extract: turn a DocChunk into candidate facts via an LLM. Pure helpers first."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from laputa.models import DocChunk, Edge, Node, Schema

SYSTEM_PROMPT = (
    "You are a knowledge-graph extractor. Read the document chunk and emit a JSON "
    'object {"nodes":[...], "edges":[...], "aliases":{...}}. '
    "Only use node_types and edge_types listed in the provided schema. "
    "For every node give id (stable slug prefixed by type, e.g. svc:payments-api), "
    "type, name, optional props, optional valid_from/valid_to (ISO dates, null = unknown). "
    "For every edge give type, from (node id), to (node id), optional valid_from/valid_to. "
    "aliases maps a canonical name to surface variants. Emit NO text outside the JSON."
)


def build_prompt(chunk: DocChunk, schema: Schema) -> str:
    node_types = ", ".join(schema.node_types.keys())
    edge_types = ", ".join(
        f"{k}({v.from_}->{v.to})" for k, v in schema.edge_types.items()
    )
    return (
        f"Allowed node_types: {node_types}\n"
        f"Allowed edge_types: {edge_types}\n\n"
        f"Source: {chunk.src}\n"
        f"Namespace: {chunk.namespace}\n\n"
        f"Document chunk:\n{chunk.text}\n"
    )


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    return date.fromisoformat(v)


def parse_response(
    raw: str, chunk: DocChunk, schema: Schema | None = None,
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    data = json.loads(raw)
    nodes: list[Node] = []
    for n in data.get("nodes", []):
        ntype = n.get("type")
        if schema is not None and not schema.is_valid_node_type(ntype):
            continue
        props = dict(n.get("props", {}))
        if "name" in n and "name" not in props:
            props["name"] = n["name"]
        nodes.append(Node(
            id=n["id"],
            type=ntype,
            ns=(chunk.namespace,),
            valid_from=_parse_date(n.get("valid_from")),
            valid_to=_parse_date(n.get("valid_to")),
            props=props,
            src=(chunk.src,),
        ))
    edges: list[Edge] = []
    for e in data.get("edges", []):
        etype = e.get("type")
        if schema is not None and not schema.is_valid_edge_type(etype):
            continue
        edges.append(Edge(
            id="",                      # assigned deterministically in resolve
            type=etype,
            **{"from": e["from"]},
            to=e["to"],
            ns=(chunk.namespace,),
            valid_from=_parse_date(e.get("valid_from")),
            valid_to=_parse_date(e.get("valid_to")),
            src=(chunk.src,),
        ))
    aliases = {k: list(v) for k, v in data.get("aliases", {}).items()}
    return nodes, edges, aliases
