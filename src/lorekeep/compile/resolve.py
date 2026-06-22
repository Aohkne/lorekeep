"""Resolve: dedup entities, validate edges, enforce ns, quarantine bad facts.

Extraction may emit the same entity under several ids (aliases). This stage
collapses them onto one canonical id, rewrites edge endpoints, drops edges whose
endpoints disappeared, and quarantines malformed facts for review.

Journal merge: loads pending journal entries, gates by confidence, merges into
the existing graph with priority: raw/ > import > agent-propose.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lorekeep.models import Edge, JournalEntry, Node


@dataclass
class ResolveResult:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)      # alias_id -> canonical_id
    quarantined: list[tuple[dict, str]] = field(default_factory=list)


def _build_alias_map(
    nodes: list[Node],
    name_aliases: dict[str, list[str]] | None,
    explicit_map: dict[str, str] | None,
) -> dict[str, str]:
    """Return alias_id -> canonical_id. Canonical = first node id seen for a name."""
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
    # 2) explicit id->id overrides win
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
) -> ResolveResult:
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
    quarantined: list[tuple[dict, str]] = []
    counter = 0
    for ed in edges:
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
) -> JournalMergeResult:
    """Gate journal entries by confidence and add to the graph.

    High (>=0.8): auto-merge. Medium (0.5 to <0.8): merge + flag for review.
    Low (<0.5): quarantine, do not merge.
    """
    result = JournalMergeResult()
    nodes_by_id: dict[str, Node] = {n.id: n for n in existing_nodes}
    new_edges: list[Edge] = []

    for entry in journal_entries:
        if entry.status != "pending":
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

        if confidence < 0.5:
            result.quarantined.append((entry, "low confidence"))
            continue

        if fact.kind == "node":
            if fact.id in nodes_by_id:
                base = nodes_by_id[fact.id]
                merged_props = {**base.props, **fact.props}
                merged_src = tuple(dict.fromkeys(base.src + fact.src))
                merged_ns = tuple(dict.fromkeys(base.ns + fact.ns))
                nodes_by_id[fact.id] = base.model_copy(
                    update={"props": merged_props, "src": merged_src, "ns": merged_ns}
                )
            else:
                nodes_by_id[fact.id] = fact
        else:
            new_edges.append(fact)

        if confidence >= 0.8:
            result.merged.append((entry, ""))
        else:
            result.flagged.append((entry, "medium confidence, flagged for review"))

    result.nodes = list(nodes_by_id.values())

    # Deduplicate + ID-regenerate edges (journal edges have empty id)
    edge_by_key: dict[tuple[str, str, str], Edge] = {}
    counter = 0
    for e in existing_edges + new_edges:
        key = (e.from_, e.to, e.type)
        if key in edge_by_key:
            # Merge props and src for duplicate edges
            existing = edge_by_key[key]
            merged_props = {**existing.props, **e.props}
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
