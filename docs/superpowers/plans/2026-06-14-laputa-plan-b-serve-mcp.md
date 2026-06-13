# Laputa Plan B — Serve (Store + Permission + MCP + Integrations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the compiled temporal knowledge graph read-only to coding agents (Claude Code, Cursor, Codex) over MCP (stdio-first), with per-namespace permission enforced at the query layer, plus `laputa mcp add` integration and a minimal Tier-2 retrieval/temporal eval harness.

**Architecture:** `GraphStore` loads `facts.jsonl` into a networkx `MultiDiGraph` and exposes temporal query methods (pure, permission-unaware). `ScopedGraph` wraps it with an `allowed_ns` set and filters every result through strict visibility rules (the single permission chokepoint). `mcp_server.py` is a FastMCP server whose tools are thin wrappers over `ScopedGraph` — tools are plain callables using a module-global scoped store, so they're testable without the MCP transport. Integrations write per-agent MCP configs; `mcp add` auto-detects the install source.

**Tech Stack:** Python 3.11+, networkx (graph), official `mcp` SDK's FastMCP (stdio), sqlite3 FTS5 (search cache), pydantic, typer, uv.

**Spec:** `docs/superpowers/specs/2026-06-14-laputa-temporal-kg-mcp-design.md` (§7 components, §8 permission, §9 temporal, §11 MCP, §12 integrations, §16 Tier-2 eval).

**Plan A (done, merged):** provides `src/laputa/models.py` (`Node`/`Edge` with `from_`/`to`/`ns`/`valid_from`/`valid_to`/`props`/`src`, `to_json_line()`, `model_dump(mode="json", by_alias=True)`), `Schema`, `eval/gold.py` (has `_read_facts`), `config.py`, `cli.py` (`compile|eval|check|version` + `_paths()`), `schema_io.load_schema`, and the `tests/fixtures/gold/payments.facts.jsonl` corpus (4 nodes, 2 edges, one temporal edge `depends_on` valid `2024-01-15`→`2025-03-01`).

---

## File Structure

```
src/laputa/
├── facts_io.py               # read_facts(path) — extracted from eval/gold._read_facts (DRY)
├── store/
│   ├── __init__.py
│   ├── graph.py              # GraphStore: load facts.jsonl -> networkx; temporal query API (pure)
│   └── fts.py                # FTSIndex (sqlite FTS5) + scan_search fallback
├── perm/
│   ├── __init__.py
│   └── ns.py                 # visibility rules + ScopedGraph (the permission chokepoint)
├── mcp_server.py             # FastMCP + 8 read tools over ScopedGraph
├── integrations/
│   ├── __init__.py
│   ├── common.py             # agent-memory snippet + resolve install command
│   ├── claude_code.py        # write .mcp.json
│   ├── cursor.py             # write .cursor/mcp.json
│   └── codex.py              # write config.toml [mcp_servers.laputa]
└── eval/
    └── retrieval.py          # Tier-2 minimal harness: scoped query correctness
└── (modify) cli.py           # add serve, mcp (add), doctor
└── (modify) config.py        # add install_source + ns mapping
└── (modify) pyproject.toml   # add mcp + networkx deps
```

**Boundaries:** `facts_io` is the shared loader (store + eval both use it). `GraphStore` is pure graph logic, no permission, no MCP. `ScopedGraph` is the only place permission is enforced. `mcp_server` tools are stateless wrappers over a module-global `ScopedGraph`. Integrations know each agent's config format and nothing else. CLI is thin.

---

## Task 1: Add `mcp` + `networkx` dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the two dependencies to `pyproject.toml`**

In the `dependencies` list, add `networkx` and `mcp`. The full list becomes:

```toml
dependencies = [
  "pydantic>=2.6",
  "pyyaml>=6.0",
  "mistune>=3.0",
  "litellm>=1.40",
  "typer>=0.12",
  "rich>=13.7",
  "networkx>=3.2",
  "mcp>=1.0",
]
```

- [ ] **Step 2: Sync and verify both import**

Run:
```bash
uv sync
uv run python -c "import networkx, mcp; from mcp.server.fastmcp import FastMCP; print(networkx.__version__)"
```
Expected: prints a networkx version (e.g. `3.x`) with no ImportError.

- [ ] **Step 3: Confirm the existing suite still passes**

Run: `uv run pytest -q`
Expected: 46 passed (no regressions from the new deps).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add networkx + mcp dependencies for Plan B (serve)"
```

---

## Task 2: Extract shared `read_facts` loader (DRY)

**Files:**
- Create: `src/laputa/facts_io.py`
- Modify: `src/laputa/eval/gold.py` (use the shared loader)

- [ ] **Step 1: Write `src/laputa/facts_io.py`**

```python
"""Shared loader for facts.jsonl -> list[Node|Edge]. Used by store + eval."""
from __future__ import annotations

import json
from pathlib import Path

from laputa.models import Edge, Node


def read_facts(path: Path) -> list[Node | Edge]:
    """Read a facts.jsonl file (one JSON object per line) into typed facts."""
    facts: list[Node | Edge] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d["kind"] == "node":
            facts.append(Node.model_validate(d))
        else:
            facts.append(Edge.model_validate(d))
    return facts
```

- [ ] **Step 2: Refactor `src/laputa/eval/gold.py` to use it**

Replace the private `_read_facts` function body and its callers. The new `gold.py`:

```python
"""Load gold + compiled facts, and define match keys for evaluation."""
from __future__ import annotations

from pathlib import Path

from laputa.facts_io import read_facts
from laputa.models import Edge, Node


def load_gold(gold_dir: Path) -> list[Node | Edge]:
    """Load every *.facts.jsonl under gold_dir."""
    facts: list[Node | Edge] = []
    for p in sorted(gold_dir.glob("**/*.facts.jsonl")):
        facts.extend(read_facts(p))
    return facts


def load_compiled(graph_dir: Path) -> list[Node | Edge]:
    return read_facts(graph_dir / "facts.jsonl")


def node_key(n: Node) -> tuple[str, str]:
    return (n.type, n.props.get("name", n.id))


def edge_key(e: Edge, nodes_by_id: dict[str, Node]) -> tuple[str, str, str]:
    f = nodes_by_id.get(e.from_)
    t = nodes_by_id.get(e.to)
    fn = f.props.get("name", e.from_) if f else e.from_
    tn = t.props.get("name", e.to) if t else e.to
    return (e.type, fn, tn)
```

- [ ] **Step 3: Run the eval + full suite**

Run: `uv run pytest -q`
Expected: 46 passed (the refactor is behavior-preserving; `read_facts` is identical to the old `_read_facts`).

- [ ] **Step 4: Commit**

```bash
git add src/laputa/facts_io.py src/laputa/eval/gold.py
git commit -m "refactor: extract shared read_facts loader (store + eval)"
```

---

## Task 3: `GraphStore` — load + `get_node` + `node_ids`

**Files:**
- Create: `src/laputa/store/__init__.py` (empty)
- Create: `src/laputa/store/graph.py`
- Test: `tests/test_graph_store.py`

- [ ] **Step 1: Write the failing test `tests/test_graph_store.py`**

```python
from pathlib import Path
from laputa.store.graph import GraphStore


def store_from_gold(fixtures: Path) -> GraphStore:
    # reuse the compiled-equal gold fixture as a working graph
    import shutil, tempfile
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    return GraphStore.from_jsonl(d / "facts.jsonl")


def test_load_and_get_node(fixtures: Path):
    g = store_from_gold(fixtures)
    assert g.node_ids() == {"svc:payments-api", "svc:auth", "team:backend", "dec:adr-007"}
    n = g.get_node("svc:payments-api")
    assert n is not None
    assert n.type == "service"
    assert n.props["name"] == "payments-api"
    assert g.get_node("missing") is None


def test_all_nodes_all_edges(fixtures: Path):
    g = store_from_gold(fixtures)
    assert len(g.all_nodes()) == 4
    assert len(g.all_edges()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'laputa.store'`).

- [ ] **Step 3: Create empty `src/laputa/store/__init__.py`**

- [ ] **Step 4: Write `src/laputa/store/graph.py`**

```python
"""GraphStore: load facts.jsonl into a networkx MultiDiGraph with temporal queries.

Pure graph logic. No permission, no MCP. Permission is applied by perm.ScopedGraph.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import networkx as nx

from laputa.facts_io import read_facts
from laputa.models import Edge, Node


def parse_date(value: str | None) -> date | None:
    if value is None or value == "":
        return None
    return date.fromisoformat(value)


class GraphStore:
    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self._G = nx.MultiDiGraph()
        for n in nodes:
            self._G.add_node(n.id, node=n)
        for e in edges:
            self._G.add_edge(e.from_, e.to, key=e.id, edge=e)

    @classmethod
    def from_jsonl(cls, path: Path) -> "GraphStore":
        facts = read_facts(path)
        nodes = [f for f in facts if isinstance(f, Node)]
        edges = [f for f in facts if isinstance(f, Edge)]
        return cls(nodes, edges)

    def node_ids(self) -> set[str]:
        return set(self._G.nodes)

    def get_node(self, id: str) -> Node | None:
        if id not in self._G:
            return None
        return self._G.nodes[id]["node"]

    def all_nodes(self) -> list[Node]:
        return [d["node"] for _, d in self._G.nodes(data=True)]

    def all_edges(self) -> list[Edge]:
        return [d["edge"] for _, _, d in self._G.edges(data=True, keys=False)]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/laputa/store/__init__.py src/laputa/store/graph.py tests/test_graph_store.py
git commit -m "feat(store): GraphStore load + get_node + node_ids"
```

---

## Task 4: `GraphStore` — `neighbors` (BFS traversal)

**Files:**
- Modify: `src/laputa/store/graph.py` (add `out_edges`, `in_edges`, `neighbors`)
- Test: `tests/test_graph_store.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_graph_store.py`**

```python
def test_out_and_in_edges(fixtures: Path):
    g = store_from_gold(fixtures)
    out = g.out_edges("svc:payments-api")
    assert len(out) == 1 and out[0].type == "depends_on"
    assert g.out_edges("svc:payments-api", edge_type="decided_by") == []
    inn = g.in_edges("svc:auth")
    assert len(inn) == 1 and inn[0].from_ == "svc:payments-api"


def test_neighbors_depth_one(fixtures: Path):
    g = store_from_gold(fixtures)
    nb = g.neighbors("svc:payments-api", depth=1)
    ids = {n.id for n in nb["nodes"]}
    assert ids == {"svc:payments-api", "svc:auth"}
    assert len(nb["edges"]) == 1


def test_neighbors_unknown_node(fixtures: Path):
    g = store_from_gold(fixtures)
    assert g.neighbors("nope") == {"nodes": [], "edges": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: FAIL (`AttributeError: 'GraphStore' object has no attribute 'out_edges'`).

- [ ] **Step 3: Append to `src/laputa/store/graph.py`** (inside the class, after `all_edges`)

```python
    def out_edges(self, id: str, edge_type: str | None = None) -> list[Edge]:
        result = []
        for _, _, d in self._G.out_edges(id, data=True, keys=False):
            e = d["edge"]
            if edge_type is None or e.type == edge_type:
                result.append(e)
        return result

    def in_edges(self, id: str, edge_type: str | None = None) -> list[Edge]:
        result = []
        for _, _, d in self._G.in_edges(id, data=True, keys=False):
            e = d["edge"]
            if edge_type is None or e.type == edge_type:
                result.append(e)
        return result

    def neighbors(self, id: str, edge_type: str | None = None, depth: int = 1) -> dict:
        """BFS over both directions up to `depth`. Returns {nodes:[Node], edges:[Edge]}."""
        if id not in self._G:
            return {"nodes": [], "edges": []}
        seen_nodes = {id}
        seen_edges: set[str] = set()
        out_nodes: list[Node] = []
        out_edges: list[Edge] = []
        frontier = [id]
        for _ in range(max(depth, 0)):
            nxt: list[str] = []
            for u in frontier:
                for e in self.out_edges(u, edge_type) + self.in_edges(u, edge_type):
                    if e.id in seen_edges:
                        continue
                    seen_edges.add(e.id)
                    out_edges.append(e)
                    other = e.to if e.from_ == u else e.from_
                    if other not in seen_nodes and other in self._G:
                        seen_nodes.add(other)
                        nxt.append(other)
                        out_nodes.append(self._G.nodes[other]["node"])
            frontier = nxt
        return {"nodes": [self.get_node(id)] + out_nodes, "edges": out_edges}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/store/graph.py tests/test_graph_store.py
git commit -m "feat(store): GraphStore out/in edges + BFS neighbors"
```

---

## Task 5: `GraphStore` — temporal `snapshot`

**Files:**
- Modify: `src/laputa/store/graph.py` (add `_active`, `snapshot`)
- Test: `tests/test_graph_store.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_graph_store.py`**

```python
from datetime import date
from laputa.store.graph import parse_date


def test_snapshot_includes_active_edge_before_valid_to(fixtures: Path):
    g = store_from_gold(fixtures)
    nodes, edges = g.snapshot(parse_date("2025-02-28"))
    edge_types = {e.type for e in edges}
    assert "depends_on" in edge_types      # still active on 2025-02-28


def test_snapshot_excludes_edge_at_valid_to(fixtures: Path):
    g = store_from_gold(fixtures)
    # depends_on valid_to=2025-03-01 is exclusive -> absent at 2025-03-01
    _, edges = g.snapshot(parse_date("2025-03-01"))
    assert "depends_on" not in {e.type for e in edges}


def test_snapshot_before_valid_from_excludes_edge(fixtures: Path):
    g = store_from_gold(fixtures)
    _, edges = g.snapshot(parse_date("2024-01-14"))
    assert "depends_on" not in {e.type for e in edges}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: FAIL (`AttributeError: ... 'snapshot'`).

- [ ] **Step 3: Append to `src/laputa/store/graph.py`**

```python
    @staticmethod
    def _active(valid_from: date | None, valid_to: date | None, t: date) -> bool:
        """Half-open [valid_from, valid_to): None means unbounded on that side."""
        if valid_from is not None and t < valid_from:
            return False
        if valid_to is not None and not (t < valid_to):
            return False
        return True

    def snapshot(self, time: date) -> tuple[list[Node], list[Edge]]:
        nodes = [n for n in self.all_nodes()
                 if self._active(n.valid_from, n.valid_to, time)]
        edges = [e for e in self.all_edges()
                 if self._active(e.valid_from, e.valid_to, time)]
        return nodes, edges
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/store/graph.py tests/test_graph_store.py
git commit -m "feat(store): temporal snapshot (half-open [from,to) validity)"
```

---

## Task 6: `GraphStore` — `history` + `changes`

**Files:**
- Modify: `src/laputa/store/graph.py` (add `history`, `changes`)
- Test: `tests/test_graph_store.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_graph_store.py`**

```python
def test_history_returns_node_and_touching_edges(fixtures: Path):
    g = store_from_gold(fixtures)
    h = g.history("svc:payments-api")
    kinds = [item["kind"] for item in h]
    assert kinds[0] == "node"
    assert "edge" in kinds
    # edges ordered by valid_from (None sorts first)
    edges = [it for it in h if it["kind"] == "edge"]
    assert edges[0]["type"] == "depends_on"


def test_changes_detects_began_and_ended(fixtures: Path):
    g = store_from_gold(fixtures)
    # depends_on valid_from=2024-01-15, valid_to=2025-03-01
    rep = g.changes(parse_date("2024-01-01"), parse_date("2025-04-01"))
    began_types = {e["type"] for e in rep["began"]}
    ended_types = {e["type"] for e in rep["ended"]}
    assert "depends_on" in began_types
    assert "depends_on" in ended_types


def test_changes_outside_window_empty(fixtures: Path):
    g = store_from_gold(fixtures)
    rep = g.changes(parse_date("2099-01-01"), parse_date("2099-02-01"))
    assert rep == {"began": [], "ended": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: FAIL (`AttributeError: ... 'history'`).

- [ ] **Step 3: Append to `src/laputa/store/graph.py`**

```python
    def history(self, id: str) -> list[dict]:
        """Node + all edges touching it, ordered by valid_from (None first)."""
        node = self.get_node(id)
        if node is None:
            return []
        touching = self.out_edges(id) + self.in_edges(id)
        touching.sort(key=lambda e: e.valid_from or date.min)
        items: list[dict] = [{"kind": "node", **node.model_dump(mode="json", by_alias=True)}]
        for e in touching:
            items.append({"kind": "edge", **e.model_dump(mode="json", by_alias=True)})
        return items

    def changes(self, from_t: date, to_t: date) -> dict:
        """Edges whose validity began or ended within [from_t, to_t)."""
        began: list[dict] = []
        ended: list[dict] = []
        for e in self.all_edges():
            ed = e.model_dump(mode="json", by_alias=True)
            if e.valid_from is not None and from_t <= e.valid_from < to_t:
                began.append(ed)
            if e.valid_to is not None and from_t <= e.valid_to < to_t:
                ended.append(ed)
        return {"began": began, "ended": ended}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_graph_store.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/store/graph.py tests/test_graph_store.py
git commit -m "feat(store): history + changes temporal queries"
```

---

## Task 7: Permission — visibility rules

**Files:**
- Create: `src/laputa/perm/__init__.py` (empty)
- Create: `src/laputa/perm/ns.py`
- Test: `tests/test_perm.py`

- [ ] **Step 1: Write the failing test `tests/test_perm.py`**

```python
from laputa.models import Node, Edge
from laputa.perm.ns import effective_ns, is_node_visible, is_edge_visible


def n(id, ns):
    return Node(id=id, type="service", ns=tuple(ns), props={"name": id})


def e(frm, to, ns):
    return Edge(id="e1", type="depends_on", **{"from": frm}, to=to, ns=tuple(ns))


def test_effective_ns_includes_public():
    assert effective_ns(["teams/backend"]) == {"teams/backend", "public"}


def test_node_visible_via_public():
    eff = effective_ns(["teams/frontend"])
    assert is_node_visible(n("x", ["public"]), eff)
    assert not is_node_visible(n("y", ["teams/backend"]), eff)


def test_edge_requires_both_endpoints_and_edge_ns():
    eff = effective_ns(["teams/backend"])
    a = n("a", ["teams/backend"])
    b = n("b", ["teams/backend"])
    c = n("c", ["teams/frontend"])          # hidden from backend
    assert is_edge_visible(e("a", "b", ["teams/backend"]), a, b, eff)
    # endpoint c hidden -> edge invisible even if edge.ns is backend
    assert not is_edge_visible(e("a", "c", ["teams/backend"]), a, c, eff)
    # edge crosses into a ns the caller lacks -> invisible
    assert not is_edge_visible(e("a", "b", ["teams/frontend"]), a, b, eff)


def test_deny_default_when_endpoint_none():
    eff = effective_ns(["teams/backend"])
    a = n("a", ["teams/backend"])
    assert not is_edge_visible(e("a", "ghost", ["teams/backend"]), a, None, eff)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_perm.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'laputa.perm'`).

- [ ] **Step 3: Create empty `src/laputa/perm/__init__.py`**

- [ ] **Step 4: Write `src/laputa/perm/ns.py`** (rules only — `ScopedGraph` comes in Task 8)

```python
"""Namespace permission rules. Deny-by-default.

Node visible iff ns ∩ effective_ns ≠ ∅. Edge visible iff BOTH endpoints visible
AND edge.ns ∩ effective_ns ≠ ∅. effective_ns = allowed ∪ {public}.
"""
from __future__ import annotations

from laputa.models import Edge, Node


def effective_ns(allowed_ns) -> set[str]:
    return set(allowed_ns) | {"public"}


def is_node_visible(node: Node | None, eff_ns: set[str]) -> bool:
    if node is None:
        return False
    return bool(set(node.ns) & eff_ns)


def is_edge_visible(
    edge: Edge, from_node: Node | None, to_node: Node | None, eff_ns: set[str]
) -> bool:
    if not is_node_visible(from_node, eff_ns):
        return False
    if not is_node_visible(to_node, eff_ns):
        return False
    return bool(set(edge.ns) & eff_ns)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_perm.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add src/laputa/perm/__init__.py src/laputa/perm/ns.py tests/test_perm.py
git commit -m "feat(perm): namespace visibility rules (deny-default, edge needs both endpoints)"
```

---

## Task 8: `ScopedGraph` — filtered `get_node` + `neighbors`

**Files:**
- Modify: `src/laputa/perm/ns.py` (add `ScopedGraph`)
- Test: `tests/test_perm.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_perm.py`**

```python
from laputa.store.graph import GraphStore


def store_with_cross_ns(tmp_path):
    # backend node a, frontend node c, edge a->c tagged backend (cross-ns)
    import json
    facts = [
        {"kind": "node", "id": "a", "type": "service", "ns": ["teams/backend"], "props": {"name": "a"}},
        {"kind": "node", "id": "c", "type": "service", "ns": ["teams/frontend"], "props": {"name": "c"}},
        {"kind": "edge", "id": "e1", "type": "depends_on", "from": "a", "to": "c", "ns": ["teams/backend"]},
    ]
    p = tmp_path / "facts.jsonl"
    p.write_text("\n".join(json.dumps(f) for f in facts))
    return GraphStore.from_jsonl(p)


def test_scoped_get_node_hides_other_ns(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    assert scoped.get_node("a") is not None
    assert scoped.get_node("c") is None              # frontend hidden


def test_scoped_neighbors_hides_cross_ns_edge(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    nb = scoped.neighbors("a", depth=1)
    ids = {n.id for n in nb["nodes"]}
    assert ids == {"a"}                              # c hidden, edge a->c dropped
    assert nb["edges"] == []


def test_scoped_public_caller_sees_public_only(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, [])                       # only public
    assert scoped.get_node("a") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_perm.py -v`
Expected: FAIL (`ImportError: cannot import name 'ScopedGraph'`).

- [ ] **Step 3: Append `ScopedGraph` to `src/laputa/perm/ns.py`**

```python
from laputa.store.graph import GraphStore


class ScopedGraph:
    """The single permission chokepoint: wraps a GraphStore and filters every query."""

    def __init__(self, graph: GraphStore, allowed_ns) -> None:
        self._g = graph
        self._allowed = set(allowed_ns)
        self._eff = effective_ns(allowed_ns)

    @property
    def allowed_namespaces(self) -> set[str]:
        return self._allowed

    def _node_visible(self, node: Node | None) -> bool:
        return is_node_visible(node, self._eff)

    def get_node(self, id: str) -> Node | None:
        node = self._g.get_node(id)
        return node if self._node_visible(node) else None

    def neighbors(self, id: str, edge_type: str | None = None, depth: int = 1) -> dict:
        start = self._g.get_node(id)
        if not self._node_visible(start):
            return {"nodes": [], "edges": []}
        raw = self._g.neighbors(id, edge_type, depth)
        visible_ids = {n.id for n in raw["nodes"] if self._node_visible(n)}
        visible_ids.add(id)
        nodes = [self._g.get_node(nid) for nid in sorted(visible_ids)]
        edges = [
            e for e in raw["edges"]
            if e.from_ in visible_ids and e.to in visible_ids and bool(set(e.ns) & self._eff)
        ]
        return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_perm.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/perm/ns.py tests/test_perm.py
git commit -m "feat(perm): ScopedGraph filtered get_node + neighbors"
```

---

## Task 9: `ScopedGraph` — `snapshot` / `history` / `changes` / `list_namespaces`

**Files:**
- Modify: `src/laputa/perm/ns.py` (add methods)
- Test: `tests/test_perm.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_perm.py`**

```python
def test_scoped_snapshot_filters_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    from laputa.store.graph import parse_date
    scoped = ScopedGraph(g, ["teams/frontend"])
    nodes, edges = scoped.snapshot(parse_date("2024-01-15"))
    assert {n.id for n in nodes} == {"c"}            # backend hidden


def test_scoped_history_empty_for_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/frontend"])
    assert scoped.history("a") == []                 # a hidden


def test_scoped_changes_filters_edges_to_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    from laputa.store.graph import parse_date
    scoped = ScopedGraph(g, ["teams/backend"])
    rep = scoped.changes(parse_date("2000-01-01"), parse_date("2100-01-01"))
    # edge a->c: a visible, c hidden -> edge dropped
    assert rep["began"] == [] and rep["ended"] == []


def test_list_namespaces(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/backend"])
    assert scoped.list_namespaces() == ["public", "teams/backend"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_perm.py -v`
Expected: FAIL (`AttributeError: 'ScopedGraph' object has no attribute 'snapshot'`).

- [ ] **Step 3: Append to `ScopedGraph` in `src/laputa/perm/ns.py`**

```python
    from datetime import date

    def snapshot(self, time) -> tuple[list[Node], list[Edge]]:
        nodes, edges = self._g.snapshot(time)
        vis_nodes = [n for n in nodes if self._node_visible(n)]
        vis_ids = {n.id for n in vis_nodes}
        vis_edges = [
            e for e in edges
            if e.from_ in vis_ids and e.to in vis_ids
            and bool(set(e.ns) & self._eff)
        ]
        return vis_nodes, vis_edges

    def history(self, id: str) -> list[dict]:
        if not self._node_visible(self._g.get_node(id)):
            return []
        items = self._g.history(id)
        out: list[dict] = []
        for it in items:
            if it["kind"] == "node":
                out.append(it)
            else:
                # only include edges whose both endpoints are visible to caller
                f = self._g.get_node(it["from"])
                t = self._g.get_node(it["to"])
                if is_edge_visible(_edge_from_dict(it), f, t, self._eff):
                    out.append(it)
        return out

    def changes(self, from_t, to_t) -> dict:
        rep = self._g.changes(from_t, to_t)
        result = {"began": [], "ended": []}
        for key in ("began", "ended"):
            for ed in rep[key]:
                f = self._g.get_node(ed["from"])
                t = self._g.get_node(ed["to"])
                if is_edge_visible(_edge_from_dict(ed), f, t, self._eff):
                    result[key].append(ed)
        return result

    def list_namespaces(self) -> list[str]:
        return sorted(self._eff)


def _edge_from_dict(d: dict) -> Edge:
    return Edge.model_validate(d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_perm.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/perm/ns.py tests/test_perm.py
git commit -m "feat(perm): ScopedGraph snapshot/history/changes/list_namespaces"
```

---

## Task 10: FTS index + fallback scan

**Files:**
- Create: `src/laputa/store/fts.py`
- Test: `tests/test_fts.py`

- [ ] **Step 1: Write the failing test `tests/test_fts.py`**

```python
from pathlib import Path
from laputa.models import Node
from laputa.store.fts import FTSIndex, scan_search, node_text


def nd(id, name, lang=None):
    props = {"name": name}
    if lang:
        props["lang"] = lang
    return Node(id=id, type="service", ns=("teams/backend",), props=props)


def test_node_text_concatenates_id_name_props():
    t = node_text(nd("svc:x", "auth", "go"))
    assert "svc:x" in t and "auth" in t and "go" in t


def test_scan_search_substring(tmp_path: Path):
    nodes = [nd("svc:a", "payments-api"), nd("svc:b", "auth")]
    assert scan_search(nodes, "pay") == ["svc:a"]
    assert scan_search(nodes, "auth") == ["svc:b"]
    assert scan_search(nodes, "zzz") == []


def test_fts_index_build_and_match(tmp_path: Path):
    idx = FTSIndex(tmp_path / "fts.sqlite")
    idx.build([nd("svc:a", "payments"), nd("svc:b", "auth")])
    assert "svc:a" in idx.search("payments")
    assert idx.search("payments") == ["svc:a"]
    assert idx.search("nomatch*") == []
    idx.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fts.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `src/laputa/store/fts.py`**

```python
"""Optional FTS5 search cache over node text. Falls back to in-memory scan."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from laputa.models import Node


def node_text(n: Node) -> str:
    parts = [n.id, n.type]
    if "name" in n.props:
        parts.append(str(n.props["name"]))
    for k, v in n.props.items():
        if k != "name":
            parts.append(str(v))
    return " ".join(parts)


class FTSIndex:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS nodes USING fts5(id, text)"
        )

    def build(self, nodes: list[Node]) -> None:
        self.conn.execute("DELETE FROM nodes")
        self.conn.executemany(
            "INSERT INTO nodes(id, text) VALUES(?, ?)",
            [(n.id, node_text(n)) for n in nodes],
        )
        self.conn.commit()

    def search(self, query: str, limit: int = 10) -> list[str]:
        cur = self.conn.execute(
            "SELECT id FROM nodes WHERE nodes MATCH ? LIMIT ?",
            (query, limit),
        )
        return [row[0] for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def scan_search(nodes: list[Node], query: str, limit: int = 10) -> list[str]:
    q = query.lower()
    out: list[str] = []
    for n in nodes:
        if q in node_text(n).lower():
            out.append(n.id)
            if len(out) >= limit:
                break
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fts.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/store/fts.py tests/test_fts.py
git commit -m "feat(store): FTS5 index + fallback scan search"
```

---

## Task 11: `GraphStore.search` (fts or scan) + ScopedGraph.search

**Files:**
- Modify: `src/laputa/store/graph.py` (add `search`)
- Modify: `src/laputa/perm/ns.py` (add `ScopedGraph.search`)
- Test: `tests/test_graph_store.py` + `tests/test_perm.py` (append)

- [ ] **Step 1: Append failing test to `tests/test_graph_store.py`**

```python
def test_store_search_scan(fixtures: Path):
    g = store_from_gold(fixtures)
    ids = g.search("payments")
    assert "svc:payments-api" in ids
```

- [ ] **Step 2: Append failing test to `tests/test_perm.py`**

```python
def test_scoped_search_filters_hidden(tmp_path):
    g = store_with_cross_ns(tmp_path)
    from laputa.perm.ns import ScopedGraph
    scoped = ScopedGraph(g, ["teams/frontend"])
    # search 'a' would match node a (backend) and c (frontend name 'c' has no 'a'? c text has 'c')
    # node a is backend -> hidden; only visible results returned
    res = scoped.search("a")
    assert "a" not in res
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_graph_store.py tests/test_perm.py -v`
Expected: FAIL (`AttributeError: ... 'search'`).

- [ ] **Step 4: Append to `GraphStore` in `src/laputa/store/graph.py`**

```python
    def search(self, query: str, limit: int = 10, fts=None) -> list[str]:
        """Return node ids matching query. Uses an FTSIndex if given, else scan."""
        if fts is not None:
            return fts.search(query, limit)
        from laputa.store.fts import scan_search
        return scan_search(self.all_nodes(), query, limit)
```

- [ ] **Step 5: Append to `ScopedGraph` in `src/laputa/perm/ns.py`**

```python
    def search(self, query: str, limit: int = 10, fts=None) -> list[str]:
        ids = self._g.search(query, limit * 3, fts)   # over-fetch then filter
        return [nid for nid in ids if self._node_visible(self._g.get_node(nid))][:limit]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_graph_store.py tests/test_perm.py -v`
Expected: PASS (store 12, perm 13).

- [ ] **Step 7: Commit**

```bash
git add src/laputa/store/graph.py src/laputa/perm/ns.py tests/test_graph_store.py tests/test_perm.py
git commit -m "feat(store): search via FTS or scan, ScopedGraph.search filtered"
```

---

## Task 12: MCP server — configure + read tools

**Files:**
- Create: `src/laputa/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test `tests/test_mcp_server.py`**

```python
import shutil, tempfile
from pathlib import Path
import laputa.mcp_server as ms


def setup_server(fixtures: Path, allowed):
    d = Path(tempfile.mkdtemp())
    shutil.copy(fixtures / "gold/payments.facts.jsonl", d / "facts.jsonl")
    ms.configure(graph_dir=d, allowed_ns=allowed, schema_path=fixtures / "schema.json")
    return d


def test_get_node_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.get_node("svc:payments-api")
    assert r["id"] == "svc:payments-api"
    assert r["type"] == "service"


def test_get_node_hidden_returns_error(fixtures: Path):
    setup_server(fixtures, ["teams/frontend"])
    r = ms.get_node("svc:payments-api")
    assert "error" in r


def test_neighbors_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.neighbors("svc:payments-api", depth=1)
    assert {n["id"] for n in r["nodes"]} == {"svc:payments-api", "svc:auth"}


def test_schema_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.schema()
    assert "node_types" in r and "service" in r["node_types"]


def test_list_namespaces_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    assert ms.list_namespaces() == ["public", "teams/backend"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `src/laputa/mcp_server.py`**

```python
"""FastMCP server exposing the scoped temporal graph, read-only.

Tools are plain module functions using a module-global ScopedGraph set by
configure(). @mcp.tool() registers them with FastMCP but they remain directly
callable, so tests invoke them without the MCP transport.
"""
from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from laputa.models import Schema
from laputa.perm.ns import ScopedGraph
from laputa.schema_io import load_schema
from laputa.store.graph import GraphStore, parse_date

mcp = FastMCP("laputa")

_scope: ScopedGraph | None = None
_schema: Schema | None = None


def configure(graph_dir, allowed_ns, schema_path=None, fts_path=None) -> None:
    """Load the graph + schema and build the scoped view. Called once at startup."""
    global _scope, _schema
    store = GraphStore.from_jsonl(Path(graph_dir) / "facts.jsonl")
    _schema = load_schema(schema_path) if schema_path else None
    _scope = ScopedGraph(store, allowed_ns)


def _require():
    if _scope is None:
        raise RuntimeError("mcp_server not configured; call configure() first")
    return _scope


@mcp.tool()
def get_node(id: str) -> dict:
    """Return a node by id (props + provenance), or error if absent/out of scope."""
    node = _require().get_node(id)
    if node is None:
        return {"error": "not found or out of scope"}
    return node.model_dump(mode="json", by_alias=True)


@mcp.tool()
def neighbors(id: str, edge_type: str = "", depth: int = 1) -> dict:
    """Traverse neighbors up to depth (both directions), scoped to the caller."""
    scoped = _require()
    res = scoped.neighbors(id, edge_type or None, depth)
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in res["nodes"]],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in res["edges"]],
    }


@mcp.tool()
def schema() -> dict:
    """Return the graph schema (node/edge types)."""
    if _schema is None:
        return {"error": "no schema loaded"}
    return _schema.model_dump(mode="json", by_alias=True)


@mcp.tool()
def list_namespaces() -> list:
    """Namespaces visible to this caller."""
    return _require().list_namespaces()


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): server configure + get_node/neighbors/schema/list_namespaces tools"
```

---

## Task 13: MCP server — temporal + search tools

**Files:**
- Modify: `src/laputa/mcp_server.py` (add `at_time`, `history`, `changes`, `search`)
- Test: `tests/test_mcp_server.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_mcp_server.py`**

```python
def test_at_time_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.at_time("2025-02-28")
    types = {e["type"] for e in r["edges"]}
    assert "depends_on" in types
    r2 = ms.at_time("2025-03-01")
    assert "depends_on" not in {e["type"] for e in r2["edges"]}


def test_history_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    h = ms.history("svc:payments-api")
    assert h[0]["kind"] == "node"


def test_changes_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.changes("2024-01-01", "2025-04-01")
    assert "depends_on" in {e["type"] for e in r["began"]}


def test_search_tool(fixtures: Path):
    setup_server(fixtures, ["teams/backend"])
    r = ms.search("payments")
    assert "svc:payments-api" in r
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'at_time'`).

- [ ] **Step 3: Append to `src/laputa/mcp_server.py`** (before the `if __name__` block)

```python
@mcp.tool()
def at_time(time: str) -> dict:
    """Snapshot of facts valid at an ISO date (half-open [valid_from, valid_to))."""
    scoped = _require()
    nodes, edges = scoped.snapshot(parse_date(time))
    return {
        "nodes": [n.model_dump(mode="json", by_alias=True) for n in nodes],
        "edges": [e.model_dump(mode="json", by_alias=True) for e in edges],
    }


@mcp.tool()
def history(id: str) -> list:
    """All versions of an entity + edges touching it, ordered by valid_from."""
    return _require().history(id)


@mcp.tool()
def changes(from_t: str, to_t: str) -> dict:
    """Edges whose validity began or ended within [from_t, to_t)."""
    return _require().changes(parse_date(from_t), parse_date(to_t))


@mcp.tool()
def search(query: str, limit: int = 10) -> list:
    """Text search over node ids/props, scoped to the caller."""
    return _require().search(query, limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): at_time/history/changes/search tools"
```

---

## Task 14: CLI `serve` command

**Files:**
- Modify: `src/laputa/cli.py` (add `serve`)
- Modify: `src/laputa/config.py` (add `ns` field)
- Test: `tests/test_serve_cli.py`

- [ ] **Step 1: Add `ns` to config. Modify the `Config` model in `src/laputa/config.py`**

Add a `NsConfig` and wire it. The new `config.py` (append after `CompileConfig`):

```python
class NsConfig(BaseModel):
    default: list[str] = Field(default_factory=lambda: ["public"])
    token_map: dict[str, list[str]] = Field(default_factory=dict)
```

And change `Config` to include it:

```python
class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    ns: "NsConfig" = Field(default_factory=NsConfig)
```

(Define `NsConfig` before `Config`. The `default` is `["public"]` so a caller with no `LAPUTA_NS` sees only public facts.)

- [ ] **Step 2: Write the failing test `tests/test_serve_cli.py`**

```python
from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_serve_requires_configured_graph(tmp_path: Path, fixtures: Path, monkeypatch):
    # serve calls mcp.run() which blocks; instead test that configure is reachable
    # by invoking serve with a bad graph dir -> it should fail fast, not hang.
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LAPUTA_OUT", str(out))
    monkeypatch.setenv("LAPUTA_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LAPUTA_NS", "teams/backend")

    # Patch mcp.run to a no-op so the CLI returns without blocking.
    import laputa.mcp_server as ms
    ran = {"ok": False}

    class FakeMCP:
        def run(self, transport=None):
            ran["ok"] = True
    monkeypatch.setattr(ms, "mcp", FakeMCP())

    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.stdout
    assert ran["ok"] is True
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_serve_cli.py -v`
Expected: FAIL (`Error: No such command 'serve'`).

- [ ] **Step 4: Append `serve` to `src/laputa/cli.py`** (before the `if __name__` block)

```python
@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio (default) | http"),
) -> None:
    """Serve the scoped graph over MCP."""
    p = _paths()
    raw_ns = os.environ.get("LAPUTA_NS")
    if raw_ns:
        allowed = [x.strip() for x in raw_ns.split(",") if x.strip()]
    else:
        allowed = load_config(p["config"]).ns.default
    from laputa.mcp_server import configure, mcp
    configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"])
    mcp.run(transport=transport)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_serve_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/laputa/cli.py src/laputa/config.py tests/test_serve_cli.py
git commit -m "feat(cli): serve command (stdio MCP) + ns config"
```

---

## Task 15: Integrations — config writers (Claude Code / Cursor / Codex)

**Files:**
- Create: `src/laputa/integrations/__init__.py` (empty)
- Create: `src/laputa/integrations/common.py`
- Create: `src/laputa/integrations/claude_code.py`
- Create: `src/laputa/integrations/cursor.py`
- Create: `src/laputa/integrations/codex.py`
- Test: `tests/test_integrations.py`

- [ ] **Step 1: Write the failing test `tests/test_integrations.py`**

```python
import json
from pathlib import Path
from laputa.integrations.common import resolve_command, agent_memory_snippet
from laputa.integrations import claude_code, cursor, codex


def test_resolve_command_pypi():
    assert resolve_command(None) == ("uvx", ["laputa", "serve", "--transport", "stdio"])
    assert resolve_command("pypi") == ("uvx", ["laputa", "serve", "--transport", "stdio"])


def test_resolve_command_local():
    assert resolve_command("local") == ("laputa", ["serve", "--transport", "stdio"])


def test_resolve_command_git():
    cmd, args = resolve_command("git+https://github.com/x/laputa.git")
    assert cmd == "uvx"
    assert args[:2] == ["--from", "git+https://github.com/x/laputa.git"]
    assert "serve" in args


def test_claude_writes_mcp_json(tmp_path: Path):
    claude_code.write_config(tmp_path, "uvx", ["laputa", "serve", "--transport", "stdio"],
                             ns="teams/backend")
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["laputa"]["command"] == "uvx"
    assert data["mcpServers"]["laputa"]["env"]["LAPUTA_NS"] == "teams/backend"


def test_cursor_writes_mcp_json(tmp_path: Path):
    cursor.write_config(tmp_path, "uvx", ["laputa", "serve", "--transport", "stdio"], ns=None)
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "laputa" in data["mcpServers"]


def test_codex_writes_toml(tmp_path: Path):
    codex.write_config(tmp_path, "uvx", ["laputa", "serve", "--transport", "stdio"],
                       ns="teams/backend")
    text = (tmp_path / "config.toml").read_text()
    assert "[mcp_servers.laputa]" in text
    assert 'command = "uvx"' in text
    assert 'LAPUTA_NS = "teams/backend"' in text


def test_agent_memory_snippet_mentions_provenance():
    s = agent_memory_snippet()
    assert "src" in s and "namespace" in s.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_integrations.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Create empty `src/laputa/integrations/__init__.py`**

- [ ] **Step 4: Write `src/laputa/integrations/common.py`**

```python
"""Shared integration helpers: resolve install command + agent-memory snippet."""
from __future__ import annotations


def resolve_command(install_source: str | None) -> tuple[str, list[str]]:
    """Return (command, args) to launch `laputa serve --transport stdio`."""
    serve_args = ["serve", "--transport", "stdio"]
    if not install_source or install_source == "pypi":
        return ("uvx", ["laputa", *serve_args])
    if install_source == "local":
        return ("laputa", serve_args)
    # anything else (git+URL, local path) -> uvx --from <source>
    return ("uvx", ["--from", install_source, "laputa", *serve_args])


def agent_memory_snippet() -> str:
    return (
        "## Laputa knowledge base (MCP)\n"
        "Before answering architecture/code/domain questions, query Laputa:\n"
        "search(q) -> get_node(id) -> neighbors / at_time / history as needed.\n"
        "Always cite `src` provenance. Knowledge is namespace-scoped - if a fact is\n"
        "missing, it may be outside your scope, not nonexistent.\n"
    )
```

- [ ] **Step 5: Write `src/laputa/integrations/claude_code.py`**

```python
"""Claude Code MCP config writer (.mcp.json)."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    entry = {"command": command, "args": args}
    if ns:
        entry["env"] = {"LAPUTA_NS": ns}
    path = Path(target_dir) / ".mcp.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
    servers = existing.get("mcpServers", {})
    servers["laputa"] = entry
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path
```

- [ ] **Step 6: Write `src/laputa/integrations/cursor.py`**

```python
"""Cursor MCP config writer (.cursor/mcp.json)."""
from __future__ import annotations

import json
from pathlib import Path


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    entry = {"command": command, "args": args}
    if ns:
        entry["env"] = {"LAPUTA_NS": ns}
    d = Path(target_dir) / ".cursor"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "mcp.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text())
    servers = existing.get("mcpServers", {})
    servers["laputa"] = entry
    path.write_text(json.dumps({"mcpServers": servers}, indent=2))
    return path
```

- [ ] **Step 7: Write `src/laputa/integrations/codex.py`**

```python
"""Codex MCP config writer (config.toml [mcp_servers.laputa])."""
from __future__ import annotations

from pathlib import Path


def _toml_quote_list(items: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def write_config(target_dir: Path, command: str, args: list[str], ns: str | None) -> Path:
    lines = [
        "[mcp_servers.laputa]",
        f'command = "{command}"',
        f"args = {_toml_quote_list(args)}",
    ]
    if ns:
        lines.append(f'env = {{ LAPUTA_NS = "{ns}" }}')
    path = Path(target_dir) / "config.toml"
    # append (preserve existing sections) with a blank separator
    sep = "\n\n" if path.exists() else ""
    with path.open("a") as f:
        f.write(sep + "\n".join(lines) + "\n")
    return path
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_integrations.py -v`
Expected: PASS (7 passed).

- [ ] **Step 9: Commit**

```bash
git add src/laputa/integrations/ tests/test_integrations.py
git commit -m "feat(integrations): claude_code/cursor/codex config writers + common"
```

---

## Task 16: CLI `mcp add` command

**Files:**
- Modify: `src/laputa/config.py` (add `install_source`)
- Modify: `src/laputa/cli.py` (add `mcp` subgroup with `add`)
- Test: `tests/test_mcp_add_cli.py`

- [ ] **Step 1: Add `install_source` to `Config` in `src/laputa/config.py`**

Add a top-level field to `Config`:

```python
class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    ns: NsConfig = Field(default_factory=NsConfig)
    install_source: str | None = None      # pypi | local | git+URL | path
```

- [ ] **Step 2: Write the failing test `tests/test_mcp_add_cli.py`**

```python
from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_mcp_add_claude_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LAPUTA_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: local\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "claude", "--ns", "teams/backend"])
    assert result.exit_code == 0, result.stdout
    import json
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["laputa"]["command"] == "laputa"
    assert "memory snippet" in result.stdout.lower() or "snippet" in result.stdout.lower()


def test_mcp_add_codex_user(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LAPUTA_CONFIG", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("install_source: git+https://github.com/x/laputa.git\n")
    result = runner.invoke(app, ["mcp", "add", "--agent", "codex", "--scope", "user"])
    assert result.exit_code == 0, result.stdout
    text = (tmp_path / "config.toml").read_text()
    assert "--from" in text and "git+https://github.com/x/laputa.git" in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_add_cli.py -v`
Expected: FAIL (`Error: No such command 'mcp'`).

- [ ] **Step 4: Add the `mcp` subgroup to `src/laputa/cli.py`** (before the `if __name__` block)

```python
mcp_app = typer.Typer(help="Coding-agent integration.")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Option(..., "--agent", help="claude | cursor | codex"),
    scope: str = typer.Option("project", "--scope", help="project | user"),
    ns: str = typer.Option(None, "--ns", help="namespace to scope the agent to"),
) -> None:
    """Write the agent's MCP config + print an agent-memory snippet."""
    from laputa.integrations import claude_code, codex, cursor
    from laputa.integrations.common import agent_memory_snippet, resolve_command

    p = _paths()
    config = load_config(p["config"])
    command, args = resolve_command(config.install_source)

    target = Path.cwd() if scope == "project" else Path.home()
    writers = {"claude": claude_code, "cursor": cursor, "codex": codex}
    if agent not in writers:
        typer.echo(f"unknown agent: {agent} (choose claude|cursor|codex)")
        raise typer.Exit(code=1)
    written = writers[agent].write_config(target, command, args, ns)
    typer.echo(f"wrote {agent} config -> {written}")
    typer.echo("\n" + agent_memory_snippet())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_add_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/laputa/config.py src/laputa/cli.py tests/test_mcp_add_cli.py
git commit -m "feat(cli): mcp add command (claude/cursor/codex, install-source aware)"
```

---

## Task 17: CLI `doctor` command

**Files:**
- Modify: `src/laputa/cli.py` (add `doctor`)
- Test: `tests/test_doctor_cli.py`

- [ ] **Step 1: Write the failing test `tests/test_doctor_cli.py`**

```python
import shutil
from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_doctor_ok(tmp_path: Path, fixtures: Path, monkeypatch):
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    monkeypatch.setenv("LAPUTA_OUT", str(out))
    monkeypatch.setenv("LAPUTA_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LAPUTA_NS", "teams/backend")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "all checks passed" in result.stdout.lower()


def test_doctor_missing_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LAPUTA_OUT", str(tmp_path / "nope"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "facts.jsonl not found" in result.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor_cli.py -v`
Expected: FAIL (`Error: No such command 'doctor'`).

- [ ] **Step 3: Append `doctor` to `src/laputa/cli.py`** (before the `if __name__` block)

```python
@app.command()
def doctor() -> None:
    """Verify install: graph loads, schema valid, ns resolves, a tool responds."""
    p = _paths()
    problems = []

    facts_path = p["out"] / "facts.jsonl"
    if not facts_path.exists():
        typer.echo(f"FAIL: facts.jsonl not found at {facts_path}")
        raise typer.Exit(code=1)

    try:
        from laputa.store.graph import GraphStore
        store = GraphStore.from_jsonl(facts_path)
    except Exception as exc:
        typer.echo(f"FAIL: cannot load graph: {exc}")
        raise typer.Exit(code=1)

    if not p["schema"].exists():
        problems.append("schema.json missing")
    else:
        try:
            load_schema(p["schema"])
        except Exception as exc:
            problems.append(f"schema invalid: {exc}")

    raw_ns = os.environ.get("LAPUTA_NS")
    allowed = [x.strip() for x in raw_ns.split(",")] if raw_ns else load_config(p["config"]).ns.default

    try:
        from laputa.mcp_server import configure, list_namespaces
        configure(graph_dir=p["out"], allowed_ns=allowed, schema_path=p["schema"])
        ns = list_namespaces()
    except Exception as exc:
        problems.append(f"mcp configure/tool failed: {exc}")
        ns = []

    if problems:
        typer.echo("FAIL: " + "; ".join(problems))
        raise typer.Exit(code=1)

    typer.echo(
        f"all checks passed: {len(store.node_ids())} nodes, "
        f"{len(store.all_edges())} edges, namespaces={ns}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/cli.py tests/test_doctor_cli.py
git commit -m "feat(cli): doctor command (install/graph/schema/ns/tools check)"
```

---

## Task 18: Tier-2 retrieval/temporal eval (minimal harness)

**Files:**
- Create: `src/laputa/eval/retrieval.py`
- Create: `tests/fixtures/retrieval/questions.json`
- Test: `tests/test_retrieval_eval.py`

- [ ] **Step 1: Write the fixture `tests/fixtures/retrieval/questions.json`**

```json
[
  {
    "id": "q1",
    "kind": "multihop",
    "query": "neighbors of payments-api depth 1",
    "start": "svc:payments-api",
    "depth": 1,
    "expect_node_ids": ["svc:auth"]
  },
  {
    "id": "q2",
    "kind": "temporal",
    "query": "depends_on active at 2025-02-28",
    "time": "2025-02-28",
    "expect_edge_types_present": ["depends_on"]
  },
  {
    "id": "q3",
    "kind": "temporal",
    "query": "depends_on active at 2025-03-01",
    "time": "2025-03-01",
    "expect_edge_types_absent": ["depends_on"]
  }
]
```

- [ ] **Step 2: Write the failing test `tests/test_retrieval_eval.py`**

```python
from pathlib import Path
from laputa.eval.retrieval import retrieval_report


def test_retrieval_report_all_pass(fixtures: Path, tmp_path: Path):
    import shutil
    out = tmp_path / "graph"
    out.mkdir()
    shutil.copy(fixtures / "gold/payments.facts.jsonl", out / "facts.jsonl")
    rep = retrieval_report(
        graph_dir=out,
        questions_path=fixtures / "retrieval/questions.json",
        allowed_ns=["teams/backend"],
    )
    assert rep["total"] == 3
    assert rep["passed"] == 3
    assert rep["failures"] == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_retrieval_eval.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Write `src/laputa/eval/retrieval.py`**

```python
"""Tier-2 retrieval/temporal eval: minimal harness (not full benchmark datasets).

Loads a fixture graph + a small JSON question set, runs the scoped query path,
and checks expected node ids / edge-type presence per question. Full
HotpotQA/CronQuestions adaptation is deferred (spec §16 Tier 2).
"""
from __future__ import annotations

import json
from pathlib import Path

from laputa.perm.ns import ScopedGraph
from laputa.store.graph import GraphStore, parse_date


def retrieval_report(graph_dir: Path, questions_path: Path, allowed_ns) -> dict:
    store = GraphStore.from_jsonl(Path(graph_dir) / "facts.jsonl")
    scoped = ScopedGraph(store, allowed_ns)
    questions = json.loads(Path(questions_path).read_text())

    total = len(questions)
    failures = []
    for q in questions:
        ok = _check(scoped, q)
        if not ok:
            failures.append(q["id"])
    return {"total": total, "passed": total - len(failures), "failures": failures}


def _check(scoped: ScopedGraph, q: dict) -> bool:
    kind = q["kind"]
    if kind == "multihop":
        res = scoped.neighbors(q["start"], depth=q.get("depth", 1))
        got = {n.id for n in res["nodes"]}
        return set(q["expect_node_ids"]).issubset(got)
    if kind == "temporal":
        _, edges = scoped.snapshot(parse_date(q["time"]))
        types = {e.type for e in edges}
        if "expect_edge_types_present" in q:
            if not set(q["expect_edge_types_present"]).issubset(types):
                return False
        if "expect_edge_types_absent" in q:
            if set(q["expect_edge_types_absent"]) & types:
                return False
        return True
    return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_retrieval_eval.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/laputa/eval/retrieval.py tests/fixtures/retrieval/questions.json tests/test_retrieval_eval.py
git commit -m "feat(eval): Tier-2 minimal retrieval/temporal harness"
```

---

## Task 19: Docs + end-to-end smoke

**Files:**
- Create: `docs/serve.md`
- Modify: `docs/compile.md` (cross-link)
- Run: full suite + in-process MCP tool smoke + `mcp add` + `doctor`

- [ ] **Step 1: Write `docs/serve.md`**

````markdown
# Serving the knowledge graph to coding agents

## 1. Compile first

```bash
LAPUTA_PROVIDER=fake uv run laputa compile   # produces graph/facts.jsonl
```

## 2. Scope your namespace

Set `LAPUTA_NS` to the namespaces the agent may read (comma-separated). Unset
defaults to `public` (only public facts).

```bash
export LAPUTA_NS=teams/backend
```

## 3. Wire up a coding agent

```bash
uv run laputa mcp add --agent claude --ns teams/backend
uv run laputa mcp add --agent cursor
uv run laputa mcp add --agent codex  --ns teams/backend
```

This writes the agent's MCP config (`.mcp.json` / `.cursor/mcp.json` /
`config.toml`) and prints an agent-memory snippet to paste into `CLAUDE.md` /
`.cursorrules` / `AGENTS.md`. Set `install_source` in `.laputa/config.yaml`
(`pypi` | `local` | `git+URL`) so the emitted command is correct.

### Resulting configs

Claude Code (`.mcp.json`):
```json
{"mcpServers": {"laputa": {"command": "uvx",
  "args": ["laputa", "serve", "--transport", "stdio"],
  "env": {"LAPUTA_NS": "teams/backend"}}}}
```

Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.laputa]
command = "uvx"
args = ["laputa", "serve", "--transport", "stdio"]
env = { LAPUTA_NS = "teams/backend" }
```

## 4. Verify

```bash
uv run laputa doctor
```

Checks the graph loads, schema is valid, namespace resolves, and a tool responds.

## 5. Tools available (read-only, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`. Every result is filtered to your namespace;
cross-namespace edges are hidden unless both endpoints are visible.
````

- [ ] **Step 2: Cross-link from `docs/compile.md`** — append to the end of `docs/compile.md`:

```markdown

## Next: serve to agents

See [serve.md](serve.md) to expose the graph to Claude Code / Cursor / Codex over MCP.
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (Plan A 46 + Plan B new tests; ~80+ total).

- [ ] **Step 4: In-process MCP tool smoke**

Run:
```bash
LAPUTA_PROVIDER=fake uv run laputa compile
uv run python -c "
import laputa.mcp_server as ms
ms.configure(graph_dir='graph', allowed_ns=['teams/backend'], schema_path='graph/schema.json')
print('get_node:', ms.get_node('svc:payments-api')['id'])
print('neighbors:', [n['id'] for n in ms.neighbors('svc:payments-api', depth=1)['nodes']])
print('at_time 2025-02-28:', [e['type'] for e in ms.at_time('2025-02-28')['edges']])
print('list_namespaces:', ms.list_namespaces())
"
```
Expected: prints `svc:payments-api`, `[svc:payments-api, svc:auth]`, `['depends_on']`, `['public', 'teams/backend']`.

- [ ] **Step 5: `mcp add` + `doctor` smoke**

Run:
```bash
LAPUTA_CONFIG=/tmp/laputa-cfg.yaml sh -c 'echo "install_source: local" > /tmp/laputa-cfg.yaml'
LAPUTA_CONFIG=/tmp/laputa-cfg.yaml uv run laputa mcp add --agent claude --ns teams/backend
LAPUTA_NS=teams/backend uv run laputa doctor
```
Expected: `mcp add` writes `.mcp.json` + prints the snippet; `doctor` prints `all checks passed`.

- [ ] **Step 6: Commit**

```bash
git add docs/serve.md docs/compile.md
git commit -m "docs: serve quickstart (mcp add, doctor, tools) + compile cross-link"
```

---

## Self-Review (run after writing)

**Spec coverage (Plan B scope):**
- store/graph.py temporal queries (get_node/neighbors/snapshot/history/changes) → T3–T6 ✓
- store/fts.py optional FTS cache + fallback → T10–T11 ✓
- perm/ns.py visibility rules + ScopedGraph chokepoint → T7–T9 ✓
- mcp_server.py FastMCP stdio + 8 scoped read tools → T12–T13 ✓
- integrations (claude/cursor/codex) + `mcp add` → T15–T16 ✓
- `doctor` → T17 ✓
- Tier-2 retrieval/temporal eval (minimal) → T18 ✓
- stdio transport, LAPUTA_NS → allowed_ns, deny-default, edge-needs-both-endpoints → T7, T8, T14 ✓
- (out of scope, correctly): streamable-HTTP, OIDC, write tools, embeddings, wiki.md, full benchmarks ✓

**Placeholder scan:** No TBD/TODO/"add error handling". Every code step has complete code. The Tier-2 harness is intentionally minimal (documented in its module docstring + task note), not a placeholder.

**Type consistency:** `ScopedGraph` methods (`get_node`, `neighbors`, `snapshot`, `history`, `changes`, `search`, `list_namespaces`) match between `perm/ns.py` and the MCP tools that call them. `parse_date` defined in `store/graph.py`, used by snapshot/tools. `resolve_command` returns `(command, args)` consumed verbatim by all three writers. `Config.install_source` + `Config.ns.default` referenced consistently by `serve`, `mcp add`, `doctor`.
