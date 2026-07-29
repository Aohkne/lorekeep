"""Resolve: dedup entities, validate edges, enforce ns, quarantine bad facts.

Extraction may emit the same entity under several ids (aliases). This stage
collapses them onto one canonical id, rewrites edge endpoints, drops edges whose
endpoints disappeared, and quarantines malformed facts for review.

Journal merge: loads pending journal entries, gates by confidence, merges into
the existing graph with priority: raw/ > import > agent-propose.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from lorekeep.models import Edge, JournalEntry, Node, Schema


@dataclass
class ResolveResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)      # alias_id -> canonical_id
    quarantined: list[tuple[dict, str]] = field(default_factory=list)


def _normalize_id(node_id: str) -> str:
    """Canonical form for duplicate detection.

    Lowercase, ``_`` and space -> ``-``. Diacritics (Vietnamese etc.) are
    PRESERVED — ``person:nguyễn`` stays distinct from ``person:nguyen``. So
    ``concept:context_purity`` == ``concept:Context Purity`` ==
    ``concept:context-purity`` (all merge), but diacritic differences do not.
    """
    normalized = unicodedata.normalize("NFC", node_id).lower()
    return re.sub(r"[-_\s]+", "-", normalized)


def _build_alias_map(
    nodes: list[Node],
    name_aliases: dict[str, list[str]] | None,
    explicit_map: dict[str, str] | None,
) -> dict[str, str]:
    """Return alias_id -> canonical_id with deterministic normalized ids."""
    alias_map: dict[str, str] = {}
    # 1) by name: group nodes whose props.name matches an alias group's canonical
    if name_aliases:
        name_to_canonical: dict[str, str] = {}
        for nd in nodes:
            nm = nd.props.get("name")
            if not nm:
                continue
            for canonical_name, variants in name_aliases.items():
                if nm in variants:
                    canon = name_to_canonical.setdefault(canonical_name, nd.id)
                    if nd.id != canon:
                        alias_map[nd.id] = canon
    # 2) auto-merge by normalized id (case/separator variants; diacritics kept).
    #    The normalized key itself is canonical, so source ordering cannot change
    #    stored ids across devices.
    for nd in nodes:
        canon = _normalize_id(nd.id)
        if nd.id != canon and nd.id not in alias_map:
            alias_map[nd.id] = canon
    # 3) explicit id->id overrides win
    if explicit_map:
        alias_map.update(explicit_map)
    return alias_map


def _canonical(node_id: str, alias_map: dict[str, str]) -> str:
    seen: set[str] = set()
    cur = node_id
    while cur in alias_map and cur not in seen:
        seen.add(cur)
        cur = alias_map[cur]
    return cur


def resolve(
    nodes: list[Node],
    edges: list[Edge],
    name_aliases: dict[str, list[str]] | None = None,
    aliases_map: dict[str, str] | None = None,
    schema: Schema | None = None,
) -> ResolveResult:
    quarantined: list[tuple[dict, str]] = []
    if schema is not None:
        valid_nodes = []
        for node in nodes:
            if schema.is_valid_node_type(node.type):
                valid_nodes.append(node)
            else:
                quarantined.append((
                    node.model_dump(mode="json", by_alias=True),
                    f"unknown node type ({node.type})",
                ))
        nodes = valid_nodes

    alias_map = _build_alias_map(nodes, name_aliases, aliases_map)

    # collapse nodes
    canon_nodes: dict[str, Node] = {}
    for nd in nodes:
        cid = _canonical(nd.id, alias_map)
        if cid in canon_nodes:
            base = canon_nodes[cid]
            merged_props = {**base.props, **nd.props}
            merged_src = tuple(dict.fromkeys(base.src + nd.src))
            merged_ns = tuple(dict.fromkeys(base.ns + nd.ns))
            canon_nodes[cid] = base.model_copy(
                update={"props": merged_props, "src": merged_src, "ns": merged_ns}
            )
        else:
            # normalize stored node id to the canonical key so node identity and
            # dict key can never diverge (covers explicit_map to a non-node id)
            canon_nodes[cid] = nd if nd.id == cid else nd.model_copy(update={"id": cid})

    out_nodes = list(canon_nodes.values())
    node_ids = set(canon_nodes.keys())

    # rewrite + validate edges
    out_edges: list[Edge] = []
    counter = 0
    for ed in sorted(edges, key=lambda e: (e.type, e.from_, e.to, e.id)):
        f = _canonical(ed.from_, alias_map)
        t = _canonical(ed.to, alias_map)
        if f not in node_ids or t not in node_ids:
            quarantined.append((ed.model_dump(mode="json", by_alias=True),
                                f"dangling endpoint ({f}->{t})"))
            continue
        if f == t:
            quarantined.append((ed.model_dump(mode="json", by_alias=True),
                                "self-loop"))
            continue
        if schema is not None:
            from_type = canon_nodes[f].type
            to_type = canon_nodes[t].type
            if not schema.is_valid_edge_endpoints(ed.type, from_type, to_type):
                quarantined.append((
                    ed.model_dump(mode="json", by_alias=True),
                    f"invalid edge endpoints for {ed.type} "
                    f"({from_type}->{to_type})",
                ))
                continue
        counter += 1
        out_edges.append(ed.model_copy(update={
            "id": f"e_{ed.type}_{counter:04d}",
            **{"from_": f},
            "to": t,
        }))

    return ResolveResult(
        nodes=out_nodes,
        edges=out_edges,
        aliases=alias_map,
        quarantined=quarantined,
    )


@dataclass
class JournalMergeResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    merged: list[tuple[JournalEntry, str]] = field(default_factory=list)
    flagged: list[tuple[JournalEntry, str]] = field(default_factory=list)
    quarantined: list[tuple[JournalEntry, str]] = field(default_factory=list)

    @property
    def merge_count(self) -> int:
        return len(self.merged)

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    @property
    def quarantine_count(self) -> int:
        return len(self.quarantined)


def merge_journals(
    existing_nodes: list[Node],
    existing_edges: list[Edge],
    journal_entries: list[JournalEntry],
    *,
    replay_accepted: bool = False,
    schema: Schema | None = None,
) -> JournalMergeResult:
    """Gate journal entries by confidence and add to the graph.

    High (>=0.8): auto-merge. Medium (0.5 to <0.8): merge + flag for review.
    Low (<0.5): quarantine, do not merge.
    """
    result = JournalMergeResult()
    nodes_by_id: dict[str, Node] = {n.id: n for n in existing_nodes}
    new_edges: list[tuple[Edge, bool]] = []

    ordered_entries = sorted(
        journal_entries,
        key=lambda entry: (
            entry.fact.get("kind") == "edge",
            entry.entry_id or entry.proposed_at,
            entry.agent,
            entry.fact.get("id", ""),
        ),
    )
    for entry in ordered_entries:
        replaying = replay_accepted and entry.status in {"merged", "flagged"}
        if entry.status != "pending" and not replaying:
            continue
        confidence = entry.confidence
        fact_data = entry.fact
        try:
            if fact_data["kind"] == "node":
                fact = Node.model_validate(fact_data)
            else:
                fact = Edge.model_validate(fact_data)
        except Exception:
            result.quarantined.append((entry, "invalid fact schema"))
            continue

        if confidence < 0.5 and not replaying:
            result.quarantined.append((entry, "low confidence"))
            continue

        if schema is not None and fact.kind == "node":
            if not schema.is_valid_node_type(fact.type):
                result.quarantined.append((entry, f"unknown node type: {fact.type}"))
                continue

        if schema is not None and fact.kind == "edge":
            from_node = nodes_by_id.get(fact.from_)
            to_node = nodes_by_id.get(fact.to)
            if (
                from_node is None
                or to_node is None
                or not schema.is_valid_edge_endpoints(
                    fact.type, from_node.type, to_node.type,
                )
            ):
                result.quarantined.append((entry, "invalid edge endpoints"))
                continue

        if fact.kind == "node":
            if fact.id in nodes_by_id:
                base = nodes_by_id[fact.id]
                merged_props = (
                    {**fact.props, **base.props}
                    if replaying
                    else {**base.props, **fact.props}
                )
                merged_src = tuple(dict.fromkeys(base.src + fact.src))
                merged_ns = tuple(dict.fromkeys(base.ns + fact.ns))
                nodes_by_id[fact.id] = base.model_copy(
                    update={"props": merged_props, "src": merged_src, "ns": merged_ns}
                )
            else:
                nodes_by_id[fact.id] = fact
        else:
            new_edges.append((fact, replaying))

        if replaying:
            continue
        if confidence >= 0.8:
            result.merged.append((entry, ""))
        else:
            result.flagged.append((entry, "medium confidence, flagged for review"))

    result.nodes = list(nodes_by_id.values())

    # Deduplicate + ID-regenerate edges (journal edges have empty id)
    edge_by_key: dict[tuple[str, str, str], Edge] = {}
    counter = 0
    edge_inputs = [(edge, False) for edge in existing_edges] + new_edges
    for e, replaying in edge_inputs:
        key = (e.from_, e.to, e.type)
        if key in edge_by_key:
            # Merge props and src for duplicate edges
            existing = edge_by_key[key]
            merged_props = (
                {**e.props, **existing.props}
                if replaying
                else {**existing.props, **e.props}
            )
            merged_src = tuple(dict.fromkeys(existing.src + e.src))
            edge_by_key[key] = existing.model_copy(
                update={"props": merged_props, "src": merged_src}
            )
        else:
            counter += 1
            eid = e.id if e.id else f"e_{e.type}_{counter:04d}"
            edge_by_key[key] = e if e.id else e.model_copy(update={"id": eid})

    result.edges = list(edge_by_key.values())
    return result
