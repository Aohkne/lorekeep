"""Rank, temporal filter, and typed 1-hop packing. No LLM, no extra MCP tool."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from lorekeep.models import Edge, Node
from lorekeep.perm.ns import ScopedGraph
from lorekeep.store.graph import GraphStore
from lorekeep.store.rank import (
    HOP_CAP,
    filter_active_edges,
    filter_active_nodes,
    is_active,
    rank_facts,
    rank_nodes,
    type_weight,
    typed_hops,
)


def _n(nid: str, ns: tuple[str, ...] = ("backend",), **kwargs) -> Node:
    return Node(id=nid, type="service", ns=ns, props={"name": nid}, **kwargs)


def _e(
    eid: str,
    etype: str,
    frm: str,
    to: str,
    ns: tuple[str, ...] = ("backend",),
    **kwargs,
) -> Edge:
    return Edge(id=eid, type=etype, **{"from": frm}, to=to, ns=ns, **kwargs)


def test_type_weight_demotes_relates_to():
    assert type_weight("depends_on") > type_weight("relates_to")
    assert type_weight("relates_to") < type_weight("owns")
    assert type_weight("same_as") < type_weight("part_of")
    assert type_weight("relates_to") < type_weight("same_as") < type_weight("custom")
    assert type_weight("custom") < type_weight("depends_on")


def test_semantic_types_track_stock_schema():
    from lorekeep.defaults import DEFAULT_SCHEMA
    from lorekeep.store.rank import IDENTITY_EDGE_TYPES, SEMANTIC_EDGE_TYPES

    stock = set(DEFAULT_SCHEMA["edge_types"])
    assert SEMANTIC_EDGE_TYPES == stock - {"relates_to", "same_as"}
    assert IDENTITY_EDGE_TYPES == {"same_as"}
    assert "relates_to" not in SEMANTIC_EDGE_TYPES
    assert "same_as" not in SEMANTIC_EDGE_TYPES


def test_rank_facts_prefers_semantic_type_over_fts_order():
    relates = _e("e_rel", "relates_to", "a", "b", props={"description": "link"})
    depends = _e("e_dep", "depends_on", "a", "c", props={"description": "link"})
    ranked = rank_facts([relates, depends], {})
    assert [e.id for e in ranked] == ["e_dep", "e_rel"]


def test_rank_facts_prefers_nearer_center():
    near = _e("e_near", "depends_on", "hub", "leaf")
    far = _e("e_far", "depends_on", "x", "y")
    dist = {"hub": 0, "leaf": 1, "x": 3, "y": 4}
    ranked = rank_facts([far, near], dist)
    assert [e.id for e in ranked] == ["e_near", "e_far"]


def test_rank_facts_uses_nearer_endpoint():
    close = _e("e_close", "depends_on", "far_end", "hub")
    far = _e("e_far", "depends_on", "x", "y")
    dist = {"hub": 0, "far_end": 4, "x": 3, "y": 3}
    ranked = rank_facts([far, close], dist)
    assert [e.id for e in ranked] == ["e_close", "e_far"]


def test_rank_facts_keeps_fts_order_on_ties():
    a = _e("e_a", "depends_on", "a", "b")
    b = _e("e_b", "depends_on", "c", "d")
    assert [e.id for e in rank_facts([a, b], {})] == ["e_a", "e_b"]


def test_rank_nodes_by_hop_distance_then_fts_order():
    dist = {"hub": 0, "near": 1, "far": 3}
    assert rank_nodes(["far", "near", "hub", "unknown"], dist) == [
        "hub", "near", "far", "unknown",
    ]


def test_filter_active_edges_half_open():
    live = _e("live", "depends_on", "a", "b", valid_from=date(2024, 1, 1), valid_to=date(2025, 3, 1))
    assert filter_active_edges([live], date(2025, 2, 28)) == [live]
    assert filter_active_edges([live], date(2025, 3, 1)) == []
    assert filter_active_edges([live], None) == [live]


def test_filter_active_nodes_and_unbounded_window():
    bounded = _n("old", valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1))
    open_ended = _n("live")
    assert filter_active_nodes([bounded, open_ended], None) == [bounded, open_ended]
    assert [n.id for n in filter_active_nodes([bounded, open_ended], date(2026, 8, 23))] == ["live"]
    assert is_active(None, None, date(2026, 8, 23))
    assert not is_active(date(2027, 1, 1), None, date(2026, 8, 23))


def test_typed_hops_packs_semantic_one_hop_only():
    nodes = [_n("a"), _n("b"), _n("c"), _n("d"), _n("e")]
    seed = _e("e_seed", "depends_on", "a", "b", props={"description": "handshake"})
    part = _e("e_part", "part_of", "a", "c")
    owns = _e("e_owns", "owns", "d", "b")
    relates = _e("e_rel", "relates_to", "a", "d")
    same = _e("e_same", "same_as", "a", "e")
    nested = _e("e_nested", "documents", "c", "d")  # 2-hop from seed via part
    store = GraphStore(nodes, [seed, part, owns, relates, same, nested])
    hops = typed_hops(store, seed, limit=4)
    ids = [e.id for e in hops]
    assert "e_part" in ids
    assert "e_owns" in ids
    assert "e_rel" not in ids
    assert "e_same" not in ids
    assert "e_nested" not in ids
    assert "e_seed" not in ids
    assert len(hops) <= HOP_CAP


def test_typed_hops_caps_at_four():
    nodes = [_n("a"), _n("b")] + [_n(f"n{i}") for i in range(6)]
    seed = _e("e_seed", "depends_on", "a", "b")
    extras = [_e(f"e_{i}", "part_of", "a", f"n{i}") for i in range(6)]
    hops = typed_hops(GraphStore(nodes, [seed, *extras]), seed)
    assert len(hops) == 4
    assert "e_seed" not in {e.id for e in hops}


def test_typed_hops_respects_as_of_and_visible():
    seed = _e("e_seed", "depends_on", "a", "b")
    live = _e("e_live", "part_of", "a", "c")
    dead = _e(
        "e_dead", "owns", "a", "d",
        valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1),
    )
    hidden = _e("e_hid", "documents", "a", "e")
    store = GraphStore(
        [_n("a"), _n("b"), _n("c"), _n("d"), _n("e")],
        [seed, live, dead, hidden],
    )
    hops = typed_hops(
        store, seed, as_of=date(2026, 8, 23), visible={"e_seed", "e_live", "e_dead"},
    )
    assert [e.id for e in hops] == ["e_live"]


def test_library_search_facts_keeps_expired_without_as_of(fixtures: Path):
    store = GraphStore.from_jsonl(fixtures / "gold/payments.facts.jsonl")
    scoped = ScopedGraph(store, ["backend"])
    ids = [e.id for e in scoped.search_facts("uses auth to validate")]
    assert ids == ["e_dep_1"]
    empty = scoped.search_facts("uses auth to validate", as_of=date(2026, 8, 23))
    assert empty == []


def test_scoped_search_facts_demotes_relates_to():
    store = GraphStore(
        [_n("a"), _n("b"), _n("c")],
        [
            _e("e_rel", "relates_to", "a", "b", props={"description": "shared token handshake"}),
            _e("e_dep", "depends_on", "a", "c", props={"description": "token handshake"}),
        ],
    )
    ranked = ScopedGraph(store, ["backend"]).search_facts("handshake")
    assert [e.id for e in ranked] == ["e_dep", "e_rel"]


def test_scoped_search_facts_center_id_prefers_nearby():
    store = GraphStore(
        [_n("hub"), _n("leaf"), _n("x"), _n("y")],
        [
            _e("e_far", "depends_on", "x", "y", props={"description": "token handshake"}),
            _e("e_hub", "depends_on", "hub", "leaf", props={"description": "token handshake"}),
        ],
    )
    ranked = ScopedGraph(store, ["backend"]).search_facts(
        "handshake", center_id="hub",
    )
    assert [e.id for e in ranked] == ["e_hub", "e_far"]


def test_scoped_typed_hops_does_not_leak_hidden_namespace():
    store = GraphStore(
        [
            _n("a", ("teams/backend",)),
            _n("b", ("teams/backend",)),
            _n("c", ("teams/frontend",)),
        ],
        [
            _e("e_seed", "depends_on", "a", "b", ns=("teams/backend",)),
            _e("e_hid", "part_of", "a", "c", ns=("teams/frontend",)),
        ],
    )
    scoped = ScopedGraph(store, ["teams/backend"])
    hops = scoped.typed_hops(store.get_edge("e_seed"))
    assert [e.id for e in hops] == []


def test_scoped_distances_ignore_hidden_shortcuts():
    """A hidden bridge must not shorten ranking distance between visible nodes."""
    store = GraphStore(
        [
            _n("a", ("teams/backend",)),
            _n("b", ("teams/backend",)),
            _n("secret", ("teams/frontend",)),
        ],
        [
            _e("e_ab", "depends_on", "a", "b", ns=("teams/backend",)),
            _e("e_as", "part_of", "a", "secret", ns=("teams/frontend",)),
            _e("e_sb", "part_of", "secret", "b", ns=("teams/frontend",)),
        ],
    )
    dist = ScopedGraph(store, ["teams/backend"]).distances_from("a")
    assert dist == {"a": 0, "b": 1}
    assert "secret" not in dist


def test_scoped_search_nodes_as_of_and_center():
    store = GraphStore(
        [
            _n("hub"),
            _n("near"),
            _n("far"),
            _n("old", valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1)),
        ],
        [
            _e("e_hn", "depends_on", "hub", "near"),
            _e("e_nf", "depends_on", "near", "far"),
        ],
    )
    scoped = ScopedGraph(store, ["backend"])
    assert "old" in scoped.search("old")
    assert "old" not in scoped.search("old", as_of=date(2026, 8, 23))
    ranked = scoped.search("service", center_id="hub")
    assert ranked.index("near") < ranked.index("far")


def test_scoped_search_facts_limit_after_rank():
    store = GraphStore(
        [_n("a"), _n("b"), _n("c")],
        [
            _e("e_rel", "relates_to", "a", "b", props={"description": "token handshake"}),
            _e("e_dep", "depends_on", "a", "c", props={"description": "token handshake"}),
        ],
    )
    ranked = ScopedGraph(store, ["backend"]).search_facts("handshake", limit=1)
    assert [e.id for e in ranked] == ["e_dep"]


def test_scoped_distances_skip_expired_bridge():
    store = GraphStore(
        [_n("a"), _n("b"), _n("c")],
        [
            _e("e_ab", "depends_on", "a", "b"),
            _e(
                "e_bc", "depends_on", "b", "c",
                valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1),
            ),
        ],
    )
    scoped = ScopedGraph(store, ["backend"])
    assert scoped.distances_from("a") == {"a": 0, "b": 1, "c": 2}
    assert scoped.distances_from("a", as_of=date(2026, 8, 23)) == {"a": 0, "b": 1}


def test_scoped_distances_hidden_or_unknown_center():
    store = GraphStore(
        [_n("a"), _n("secret", ("teams/frontend",))],
        [_e("e1", "depends_on", "a", "secret", ns=("teams/frontend",))],
    )
    scoped = ScopedGraph(store, ["backend"])
    assert scoped.distances_from("secret") == {}
    assert scoped.distances_from("missing") == {}
    assert scoped.distances_from(None) == {}


def test_scoped_distances_resolve_alias_center():
    store = GraphStore(
        [
            Node(
                id="hub", type="service", ns=("backend",),
                props={"name": "hub", "merged_ids": ["hub-alias"]},
            ),
            _n("leaf"),
        ],
        [_e("e1", "depends_on", "hub", "leaf")],
    )
    dist = ScopedGraph(store, ["backend"]).distances_from("hub-alias")
    assert dist == {"hub": 0, "leaf": 1}


def test_scoped_typed_hops_skips_expired_when_as_of_set():
    seed = _e("e_seed", "depends_on", "a", "b")
    live = _e("e_live", "part_of", "a", "c")
    dead = _e(
        "e_dead", "owns", "a", "d",
        valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1),
    )
    store = GraphStore(
        [_n("a"), _n("b"), _n("c"), _n("d")],
        [seed, live, dead],
    )
    scoped = ScopedGraph(store, ["backend"])
    assert {e.id for e in scoped.typed_hops(seed)} == {"e_live", "e_dead"}
    assert [e.id for e in scoped.typed_hops(seed, as_of=date(2026, 8, 23))] == ["e_live"]
