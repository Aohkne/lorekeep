from datetime import date
from laputa.models import Node, Edge
from laputa.compile.resolve import resolve, ResolveResult


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
