from datetime import date
from lorekeep.models import Node, Edge
from lorekeep.compile.resolve import resolve, ResolveResult


def n(id, type="service", name=None):
    return Node(id=id, type=type, ns=("teams/backend",),
                props={"name": name} if name else {})


def e(type="depends_on", frm="svc:a", to="svc:b"):
    return Edge(id="", type=type, **{"from": frm}, to=to, ns=("teams/backend",))


def test_dedup_merges_alias_to_canonical():
    nodes = [n("svc:payments-api", name="payments-api"), n("svc:pay", name="payments-api")]
    aliases = {"payments-api": ["payments-api", "pay"]}
    r = resolve(nodes, [], aliases)
    ids = {x.id for x in r.nodes}
    assert ids == {"svc:payments-api"}            # svc:pay merged away


def test_dedup_rewrites_edge_endpoints():
    nodes = [n("svc:a"), n("svc:a2")]
    edges = [e(frm="svc:a", to="svc:a2")]
    aliases = {"a": ["a", "a2"]}                  # map by name; but ids differ -> use explicit alias id map
    # Provide an explicit id-alias map by using name keys
    r = resolve(nodes, edges, aliases_map={"svc:a2": "svc:a"})
    assert all(x.from_ == "svc:a" and x.to == "svc:a" for x in r.edges)


def test_ghost_canonical_does_not_falsely_quarantine():
    # explicit_map targets a canonical id that is NOT a node id; the merged node
    # must be normalized to that canonical id and referenced edges must survive.
    nodes = [n("svc:a"), n("svc:b")]
    edges = [e(frm="svc:a", to="svc:b")]
    r = resolve(nodes, edges, aliases_map={"svc:a": "svc:ghost", "svc:b": "svc:ghost"})
    # both collapse to one canonical node "svc:ghost"; the single edge a->b becomes
    # a self-loop on ghost and is quarantined (NOT falsely dangled).
    assert [x.id for x in r.nodes] == ["svc:ghost"]
    assert r.edges == []
    assert r.quarantined and r.quarantined[0][1] == "self-loop"


def test_dangling_edge_is_quarantined():
    nodes = [n("svc:a")]
    edges = [e(frm="svc:a", to="svc:ghost")]
    r = resolve(nodes, edges)
    assert r.edges == []
    assert len(r.quarantined) == 1
    assert "dangling" in r.quarantined[0][1]


def test_self_loop_is_quarantined():
    nodes = [n("svc:a")]
    edges = [e(frm="svc:a", to="svc:a")]
    r = resolve(nodes, edges)
    assert r.edges == []
    assert r.quarantined[0][1] == "self-loop"


def test_edge_ids_are_deterministic():
    nodes = [n("svc:a"), n("svc:b"), n("svc:c")]
    edges = [e(frm="svc:a", to="svc:b"), e(frm="svc:b", to="svc:c")]
    r = resolve(nodes, edges)
    assert [x.id for x in r.edges] == ["e_depends_on_0001", "e_depends_on_0002"]
