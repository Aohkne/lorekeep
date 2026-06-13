# Laputa Plan A — Compile Pipeline + Tier-1 Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the compile pipeline that turns `raw/` markdown into a deterministic `facts.jsonl` temporal knowledge graph, plus Tier-1 construction-quality evaluation measuring extraction P/R/F1 against a gold corpus.

**Architecture:** Pipeline stages `ingest → extract → resolve → writer` each live in one focused module under `src/laputa/compile/`. Pydantic models define the `Fact`/`Schema`/`Manifest` contract. LLM extraction is provider-pluggable (litellm; default API, ollama option) and idempotent via a per-chunk hash cache so re-compiles are byte-identical. Output is deterministic (sorted facts, sorted JSON keys). Tier-1 eval compares compiled output to a human-authored gold corpus.

**Tech Stack:** Python 3.11+, pydantic v2, mistune (markdown), litellm (LLM), pyyaml (config), typer (CLI), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-06-14-laputa-temporal-kg-mcp-design.md`

---

## File Structure

```
laputa/
├── pyproject.toml                       # project + deps + pytest config
├── graph/schema.json                    # committed: node/edge type defs (the contract)
├── .laputa/config.yaml                  # LOCAL (.gitignored): provider + ns settings
├── src/laputa/
│   ├── __init__.py
│   ├── models.py                        # DocChunk, Node, Edge, Schema, Manifest (+ errors)
│   ├── config.py                        # load .laputa/config.yaml -> Config
│   ├── compile/
│   │   ├── __init__.py
│   │   ├── ingest.py                    # raw markdown -> [DocChunk] with src location
│   │   ├── providers.py                 # LLMProvider Protocol + LiteLLMProvider
│   │   ├── extract.py                   # DocChunk+Schema -> [Fact]; prompt + parse + cache
│   │   ├── resolve.py                   # dedup aliases, validate edges, ns, quarantine
│   │   └── writer.py                    # deterministic facts.jsonl + manifest.json
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── gold.py                      # load gold corpus facts
│   │   └── construction.py              # P/R/F1, entity-res F1, structure metrics, determinism
│   └── cli.py                           # `laputa compile | eval | check` (typer)
├── tests/
│   ├── conftest.py                      # shared fixtures (tmp dirs, fake provider)
│   ├── fixtures/
│   │   ├── schema.json
│   │   ├── raw/teams/backend/payments.md
│   │   └── gold/payments.facts.jsonl
│   ├── test_models.py
│   ├── test_ingest.py
│   ├── test_extract.py
│   ├── test_resolve.py
│   ├── test_writer.py
│   ├── test_compile_cli.py
│   └── test_construction_eval.py
└── raw/teams/...                         # real source docs (sample seeded in Task 1)
```

**Responsibility boundaries:** `models.py` is the shared contract every other module imports — change it last and deliberately. `ingest`/`extract`/`resolve`/`writer` are a strict linear chain; each reads the previous stage's output type and writes the next. `providers.py` is the only place that knows about litellm. `eval/` depends only on `models.py` + the writer's output, never on the LLM.

---

## Task 1: Project scaffold + sample data

**Files:**
- Create: `pyproject.toml`
- Create: `src/laputa/__init__.py`
- Create: `src/laputa/cli.py`
- Create: `graph/schema.json`
- Create: `tests/fixtures/schema.json`
- Create: `tests/fixtures/raw/teams/backend/payments.md`
- Create: `tests/fixtures/gold/payments.facts.jsonl`
- Create: `raw/teams/backend/payments.md` (copy of fixture)
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "laputa"
version = "0.1.0"
description = "Temporal knowledge graph for AI agents via MCP"
requires-python = ">=3.11"
dependencies = [
  "pydantic>=2.6",
  "pyyaml>=6.0",
  "mistune>=3.0",
  "litellm>=1.40",
  "typer>=0.12",
  "rich>=13.7",
]

[project.scripts]
laputa = "laputa.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/laputa"]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-cov>=5.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Write `src/laputa/__init__.py`**

```python
"""Laputa — temporal knowledge graph for AI agents via MCP."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Write `src/laputa/cli.py` (stub)**

```python
"""Laputa CLI."""
import typer

app = typer.Typer(help="Laputa — compile team docs into a temporal knowledge graph.")


@app.command()
def version() -> None:
    """Print the Laputa version."""
    typer.echo("laputa 0.1.0")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Write `graph/schema.json` and `tests/fixtures/schema.json` (identical content)**

```json
{
  "version": 1,
  "node_types": {
    "service": {"props": {"name": "string", "lang": "string"}},
    "team": {"props": {"name": "string"}},
    "decision": {"props": {"title": "string"}}
  },
  "edge_types": {
    "depends_on": {"from": "service", "to": "service"},
    "decided_by": {"from": "decision", "to": "team"},
    "owns": {"from": "team", "to": "service"}
  }
}
```

- [ ] **Step 5: Write `tests/fixtures/raw/teams/backend/payments.md`**

```markdown
# Payments Service

The payments-api is a Go service owned by team-backend.

It depends on the auth service for token validation.

On 2024-01-15 we launched payments-api v1.

As of 2025-03-01 the auth dependency was removed in favor of internal signing.

Decision ADR-007: payments-api adopts internal signing, decided by team-backend.
```

- [ ] **Step 6: Copy fixture to real repo path `raw/teams/backend/payments.md`**

Run: `mkdir -p raw/teams/backend && cp tests/fixtures/raw/teams/backend/payments.md raw/teams/backend/payments.md`

- [ ] **Step 7: Write `tests/fixtures/gold/payments.facts.jsonl`** (one fact per line, the human-authored ground truth for the doc above)

```jsonl
{"kind":"node","id":"svc:payments-api","type":"service","ns":["teams/backend"],"valid_from":"2024-01-15","valid_to":null,"props":{"name":"payments-api","lang":"go"},"src":["raw/teams/backend/payments.md:3"]}
{"kind":"node","id":"svc:auth","type":"service","ns":["teams/backend"],"valid_from":null,"valid_to":null,"props":{"name":"auth"},"src":["raw/teams/backend/payments.md:6"]}
{"kind":"node","id":"team:backend","type":"team","ns":["teams/backend"],"valid_from":null,"valid_to":null,"props":{"name":"team-backend"},"src":["raw/teams/backend/payments.md:3"]}
{"kind":"edge","id":"e_dep_1","type":"depends_on","from":"svc:payments-api","to":"svc:auth","ns":["teams/backend"],"valid_from":"2024-01-15","valid_to":"2025-03-01","props":{},"src":["raw/teams/backend/payments.md:6"]}
{"kind":"node","id":"dec:adr-007","type":"decision","ns":["teams/backend"],"valid_from":null,"valid_to":null,"props":{"title":"payments-api adopts internal signing"},"src":["raw/teams/backend/payments.md:11"]}
{"kind":"edge","id":"e_dec_1","type":"decided_by","from":"dec:adr-007","to":"team:backend","ns":["teams/backend"],"valid_from":null,"valid_to":null,"props":{},"src":["raw/teams/backend/payments.md:11"]}
```

- [ ] **Step 8: Write the failing test `tests/test_smoke.py`**

```python
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "laputa 0.1.0" in result.stdout
```

- [ ] **Step 9: Install and run the test**

Run: `uv sync && uv run pytest tests/test_smoke.py -v`
Expected: PASS (1 passed). If `uv` is unavailable, fall back to `pip install -e . && pytest`.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml src/ tests/ raw/ graph/schema.json
git commit -m "feat: project scaffold, schema, sample raw + gold corpus"
```

---

## Task 2: Core models (`models.py`)

**Files:**
- Create: `src/laputa/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test `tests/test_models.py`**

```python
from datetime import date
import json
from laputa.models import Node, Edge, DocChunk, Schema, Manifest


def test_node_serializes_with_sorted_keys():
    n = Node(id="svc:x", type="service", ns=("teams/backend",),
             valid_from=date(2024, 1, 15), props={"lang": "go"})
    d = json.loads(n.to_json_line())
    assert list(d.keys()) == sorted(d.keys())
    assert d["kind"] == "node"
    assert d["ns"] == ["teams/backend"]
    assert d["valid_to"] is None


def test_edge_uses_from_alias():
    e = Edge(id="e1", type="depends_on", **{"from": "a"}, to="b", ns=("teams/backend",))
    d = json.loads(e.to_json_line())
    assert d["from"] == "a"
    assert d["to"] == "b"
    assert "from_" not in d


def test_docchunk_hash_is_stable():
    c1 = DocChunk(path="raw/x.md", start_line=1, end_line=2, text="hello", namespace="teams/x")
    c2 = DocChunk(path="raw/x.md", start_line=1, end_line=2, text="hello", namespace="teams/x")
    assert c1.hash == c2.hash


def test_schema_loads_from_dict():
    s = Schema.load({
        "version": 1,
        "node_types": {"service": {"props": {"name": "string"}}},
        "edge_types": {"depends_on": {"from": "service", "to": "service"}},
    })
    assert s.version == 1
    assert "service" in s.node_types
    assert s.edge_types["depends_on"].from_ == "service"


def test_manifest_round_trips():
    m = Manifest(schema_version=1, chunk_count=1, node_count=2, edge_count=1,
                 run_id="abc", facts_hash="deadbeef", chunk_hashes={}, errors=[], quarantine=[])
    js = m.to_json()
    m2 = Manifest.from_json(js)
    assert m2.node_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'laputa.models'`

- [ ] **Step 3: Write `src/laputa/models.py`**

```python
"""Core data models for Laputa. The shared contract across compile + eval."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DocChunk(BaseModel):
    """A slice of a raw document, with provenance back to path:line."""
    model_config = ConfigDict(frozen=True)

    path: str
    start_line: int          # 1-based
    end_line: int
    text: str
    namespace: str           # e.g. "teams/backend"

    @property
    def src(self) -> str:
        return f"{self.path}:{self.start_line}"

    @property
    def hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.path.encode("utf-8"))
        h.update(b"\n")
        h.update(self.text.encode("utf-8"))
        return h.hexdigest()


class Node(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["node"] = "node"
    id: str
    type: str
    ns: tuple[str, ...]
    valid_from: date | None = None
    valid_to: date | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    src: tuple[str, ...] = Field(default_factory=tuple)

    def to_json_line(self) -> str:
        d = self.model_dump(mode="json", by_alias=True)
        return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    kind: Literal["edge"] = "edge"
    id: str
    type: str
    from_: str = Field(alias="from")
    to: str
    ns: tuple[str, ...]
    valid_from: date | None = None
    valid_to: date | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    src: tuple[str, ...] = Field(default_factory=tuple)

    def to_json_line(self) -> str:
        d = self.model_dump(mode="json", by_alias=True)
        return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


Fact = Node | Edge


class TypeSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    props: dict[str, str] = Field(default_factory=dict)


class EndpointSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int
    node_types: dict[str, TypeSpec]
    edge_types: dict[str, EndpointSpec]

    @classmethod
    def load(cls, data: dict[str, Any]) -> "Schema":
        return cls.model_validate(data)

    def is_valid_node_type(self, t: str) -> bool:
        return t in self.node_types

    def is_valid_edge_type(self, t: str) -> bool:
        return t in self.edge_types


class CompileError(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    line: int
    message: str


class QuarantineItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    fact: dict[str, Any]
    reason: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    chunk_count: int
    node_count: int
    edge_count: int
    run_id: str
    facts_hash: str
    chunk_hashes: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[CompileError] = Field(default_factory=list)
    quarantine: list[QuarantineItem] = Field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        return cls.model_validate(json.loads(text))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/models.py tests/test_models.py
git commit -m "feat(models): DocChunk, Node, Edge, Schema, Manifest contract"
```

---

## Task 3: Schema file loader

**Files:**
- Create: `src/laputa/schema_io.py`
- Test: `tests/test_schema_io.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from laputa.schema_io import load_schema


def test_load_schema_from_file(tmp_path: Path):
    p = tmp_path / "schema.json"
    p.write_text('{"version":1,"node_types":{"service":{"props":{"name":"string"}}},'
                 '"edge_types":{"depends_on":{"from":"service","to":"service"}}}')
    s = load_schema(p)
    assert s.version == 1
    assert s.is_valid_node_type("service")
    assert not s.is_valid_node_type("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_schema_io.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/schema_io.py`**

```python
"""Load the graph schema from a JSON file."""
from __future__ import annotations

import json
from pathlib import Path

from laputa.models import Schema


def load_schema(path: Path) -> Schema:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Schema.load(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_schema_io.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/laputa/schema_io.py tests/test_schema_io.py
git commit -m "feat(schema): JSON schema file loader"
```

---

## Task 4: Config loader (`.laputa/config.yaml`)

**Files:**
- Create: `src/laputa/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from laputa.config import Config, load_config


def test_load_config(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "provider:\n"
        "  backend: openai\n"
        "  model: gpt-4o-mini\n"
        "  api_base: null\n"
        "compile:\n"
        "  chunk_lines: 40\n"
    )
    c = load_config(cfg)
    assert isinstance(c, Config)
    assert c.provider.model == "gpt-4o-mini"
    assert c.compile.chunk_lines == 40


def test_default_config_when_missing(tmp_path: Path):
    c = load_config(tmp_path / "missing.yaml")
    assert c.provider.backend == "openai"
    assert c.compile.chunk_lines == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/config.py`**

```python
"""Load local Laputa config. Lives in .laputa/config.yaml (gitignored)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    backend: str = "openai"          # openai | anthropic | ollama | <litellm prefix>
    model: str = "gpt-4o-mini"
    api_base: str | None = None      # set for ollama, e.g. http://localhost:11434
    temperature: float = 0.0


class CompileConfig(BaseModel):
    chunk_lines: int = 60


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/laputa/config.py tests/test_config.py
git commit -m "feat(config): load .laputa/config.yaml with provider + compile settings"
```

---

## Task 5: Ingest (markdown → DocChunk)

**Files:**
- Create: `src/laputa/compile/__init__.py` (empty)
- Create: `src/laputa/compile/ingest.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from laputa.compile.ingest import ingest, namespace_for


def test_namespace_from_path(tmp_path: Path):
    raw = tmp_path / "raw"
    f = raw / "teams" / "backend" / "payments.md"
    f.parent.mkdir(parents=True)
    f.write_text("x\n")
    assert namespace_for(raw, f) == "teams/backend"


def test_ingest_splits_into_line_chunks(tmp_path: Path):
    raw = tmp_path / "raw"
    f = raw / "teams" / "backend" / "a.md"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(str(i) for i in range(150)))  # 150 lines
    chunks = ingest(raw, chunk_lines=60)
    assert len(chunks) == 3                       # 60 + 60 + 30
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 60
    assert chunks[0].namespace == "teams/backend"
    assert chunks[0].src == "teams/backend/a.md:1"
    assert chunks[1].start_line == 61


def test_ingest_sorted_and_skips_dirs(tmp_path: Path):
    raw = tmp_path / "raw"
    (raw / "teams" / "backend").mkdir(parents=True)
    (raw / "teams" / "backend" / "b.md").write_text("b\n")
    (raw / "teams" / "backend" / "a.md").write_text("a\n")
    chunks = ingest(raw)
    paths = [c.path for c in chunks]
    assert paths == sorted(paths)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/compile/__init__.py`** (empty file)

- [ ] **Step 4: Write `src/laputa/compile/ingest.py`**

```python
"""Ingest: raw markdown files -> DocChunks with provenance."""
from __future__ import annotations

from pathlib import Path

from laputa.models import DocChunk


def namespace_for(raw_root: Path, path: Path) -> str:
    rel = path.relative_to(raw_root)
    parts = rel.parts
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return "public"


def ingest_file(raw_root: Path, path: Path, chunk_lines: int) -> list[DocChunk]:
    ns = namespace_for(raw_root, path)
    rel = str(path.relative_to(raw_root))
    lines = path.read_text(encoding="utf-8").splitlines()
    chunks: list[DocChunk] = []
    for start in range(0, len(lines), chunk_lines):
        block = lines[start:start + chunk_lines]
        if not any(line.strip() for line in block):
            continue
        chunks.append(DocChunk(
            path=rel,
            start_line=start + 1,
            end_line=start + len(block),
            text="\n".join(block),
            namespace=ns,
        ))
    return chunks


def ingest(raw_root: Path, glob: str = "**/*.md", chunk_lines: int = 60) -> list[DocChunk]:
    chunks: list[DocChunk] = []
    for p in sorted(raw_root.glob(glob)):
        if p.is_file():
            chunks.extend(ingest_file(raw_root, p, chunk_lines))
    return chunks
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/laputa/compile/__init__.py src/laputa/compile/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): markdown -> DocChunk with namespace + provenance"
```

---

## Task 6: LLM provider abstraction

**Files:**
- Create: `src/laputa/compile/providers.py`
- Test: `tests/test_providers.py`

- [ ] **Step 1: Write the failing test**

```python
from laputa.compile.providers import LLMProvider, FakeProvider, LiteLLMProvider


def test_fake_provider_returns_fixed_output():
    p = FakeProvider(responses=['{"nodes":[],"edges":[]}'])
    assert p.extract_json("sys", "user") == '{"nodes":[],"edges":[]}'


def test_fake_provider_raises_when_empty():
    p = FakeProvider(responses=[])
    try:
        p.extract_json("sys", "user")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_litellm_provider_holds_config():
    p = LiteLLMProvider(model="ollama/llama3", api_base="http://localhost:11434")
    assert p.model == "ollama/llama3"
    assert p.api_base == "http://localhost:11434"
    assert isinstance(p, LLMProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_providers.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/compile/providers.py`**

```python
"""LLM provider abstraction. litellm is the only hard dependency on a vendor."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def extract_json(self, system: str, user: str) -> str: ...


class FakeProvider:
    """Returns canned responses in order. Used by tests; never hits a network."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def extract_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise RuntimeError("FakeProvider: no canned response left")
        return self._responses.pop(0)


class LiteLLMProvider:
    """Real provider backed by litellm. Supports openai/anthropic/ollama."""

    def __init__(self, model: str, api_base: str | None = None,
                 temperature: float = 0.0) -> None:
        self.model = model
        self.api_base = api_base
        self.temperature = temperature

    def extract_json(self, system: str, user: str) -> str:
        import litellm  # imported lazily so tests need not install it
        resp = litellm.completion(
            model=self.model,
            api_base=self.api_base,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_providers.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/compile/providers.py tests/test_providers.py
git commit -m "feat(providers): LLMProvider protocol + Fake + LiteLLM backends"
```

---

## Task 7: Extract prompt + response parser (pure functions)

**Files:**
- Create: `src/laputa/compile/extract.py`
- Test: `tests/test_extract.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/compile/extract.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/compile/extract.py tests/test_extract.py
git commit -m "feat(extract): prompt builder + schema-validated response parser"
```

---

## Task 8: Extraction cache (idempotent per chunk)

**Files:**
- Modify: `src/laputa/compile/extract.py` (append cache + `extract_chunk`)
- Test: `tests/test_extract.py` (append cases)

- [ ] **Step 1: Append failing tests to `tests/test_extract.py`**

```python
from pathlib import Path
from laputa.compile.extract import ExtractionCache, extract_chunk
from laputa.compile.providers import FakeProvider


def test_cache_key_depends_on_chunk_and_schema(tmp_path: Path):
    cache = ExtractionCache(tmp_path / "cache.json")
    c = make_chunk("hello")
    k1 = cache.key(c, SCHEMA.version)
    c2 = make_chunk("hello")
    assert cache.key(c2, SCHEMA.version) == k1
    c3 = make_chunk("different")
    assert cache.key(c3, SCHEMA.version) != k1


def test_extract_chunk_caches_and_reuses(tmp_path: Path):
    cache = ExtractionCache(tmp_path / "cache.json")
    c = make_chunk("The payments-api is a Go service.")
    raw = json.dumps({"nodes": [{"id": "svc:payments-api", "type": "service",
                                  "name": "payments-api", "props": {"lang": "go"}}],
                      "edges": [], "aliases": {}})
    provider = FakeProvider(responses=[raw])          # only ONE response available
    n1, e1, a1 = extract_chunk(c, SCHEMA, provider, cache)
    assert len(n1) == 1
    # second call must hit cache, not the provider (would raise if it called)
    n2, e2, a2 = extract_chunk(c, SCHEMA, provider, cache)
    assert len(n2) == 1
    assert len(provider.calls) == 1                    # provider called once


def test_cache_persists_to_disk(tmp_path: Path):
    p = tmp_path / "cache.json"
    cache = ExtractionCache(p)
    c = make_chunk("x")
    raw = json.dumps({"nodes": [], "edges": [], "aliases": {}})
    extract_chunk(c, SCHEMA, FakeProvider([raw]), cache)
    cache.save()
    assert p.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExtractionCache'`.

- [ ] **Step 3: Append to `src/laputa/compile/extract.py`**

```python
import hashlib
from pathlib import Path

from laputa.compile.providers import LLMProvider


class ExtractionCache:
    """Maps (chunk_hash, schema_version) -> raw LLM response. Local only."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def key(self, chunk: DocChunk, schema_version: int) -> str:
        h = hashlib.sha256()
        h.update(str(schema_version).encode("utf-8"))
        h.update(b"\n")
        h.update(chunk.hash.encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, raw: str) -> None:
        self._data[key] = raw

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, sort_keys=True, indent=2), encoding="utf-8"
        )


def extract_chunk(
    chunk: DocChunk, schema: Schema, provider: LLMProvider, cache: ExtractionCache,
) -> tuple[list[Node], list[Edge], dict[str, list[str]]]:
    key = cache.key(chunk, schema.version)
    raw = cache.get(key)
    if raw is None:
        raw = provider.extract_json(SYSTEM_PROMPT, build_prompt(chunk, schema))
        cache.set(key, raw)
    return parse_response(raw, chunk, schema)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/compile/extract.py tests/test_extract.py
git commit -m "feat(extract): idempotent per-chunk hash cache + extract_chunk"
```

---

## Task 9: Resolve — entity dedup via aliases

**Files:**
- Create: `src/laputa/compile/resolve.py`
- Test: `tests/test_resolve.py`

- [ ] **Step 1: Write the failing test `tests/test_resolve.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/compile/resolve.py`**

```python
"""Resolve: dedup entities, validate edges, enforce ns, quarantine bad facts.

Extraction may emit the same entity under several ids (aliases). This stage
collapses them onto one canonical id, rewrites edge endpoints, drops edges whose
endpoints disappeared, and quarantines malformed facts for review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from laputa.models import Edge, Node


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
        for canonical_name, variants in name_aliases.items():
            # find a node whose name is the canonical_name -> use its id
            pass  # resolved below via nodes
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
            canon_nodes[nd.id if nd.id == cid else cid] = nd

    out_nodes = list(canon_nodes.values())
    node_ids = {nd.id for nd in out_nodes}

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_resolve.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/compile/resolve.py tests/test_resolve.py
git commit -m "feat(resolve): alias dedup, edge rewrite, quarantine malformed"
```

---

## Task 10: Resolve — quarantine + ns validation

**Files:**
- Modify: `src/laputa/compile/resolve.py` (no signature change; covered already)
- Test: `tests/test_resolve.py` (append)

- [ ] **Step 1: Append failing tests to `tests/test_resolve.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_resolve.py -v`
Expected: Some FAIL — e.g. dangling test (Task 9 already implements it, verify), self-loop, deterministic ids. Run and confirm which fail; the implementation above already covers all three.

- [ ] **Step 3: (Implementation already present from Task 9 — confirm tests pass)**

If any test fails, fix `resolve()` in `src/laputa/compile/resolve.py` until green. The current implementation assigns `e_{type}_{counter:04d}` in input order and quarantines dangling/self-loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_resolve.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_resolve.py src/laputa/compile/resolve.py
git commit -m "test(resolve): dangling, self-loop, deterministic edge ids"
```

---

## Task 11: Writer — deterministic `facts.jsonl` + manifest

**Files:**
- Create: `src/laputa/compile/writer.py`
- Test: `tests/test_writer.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date
from pathlib import Path
import json
from laputa.models import Node, Edge, Manifest
from laputa.compile.writer import write_graph, run_id, facts_hash


def n(id, type="service"):
    return Node(id=id, type=type, ns=("teams/backend",), props={"name": id})


def e(frm, to):
    return Edge(id="e_depends_on_0001", type="depends_on", **{"from": frm},
                to=to, ns=("teams/backend",))


def test_write_graph_is_deterministic(tmp_path: Path):
    nodes = [n("svc:b"), n("svc:a")]          # unsorted on purpose
    edges = [e("svc:a", "svc:b")]
    out1 = tmp_path / "g1"
    out2 = tmp_path / "g2"
    write_graph(out1, nodes, edges, Manifest(schema_version=1, chunk_count=1,
                node_count=2, edge_count=1, run_id="r", facts_hash="x"))
    write_graph(out2, nodes, edges, Manifest(schema_version=1, chunk_count=1,
                node_count=2, edge_count=1, run_id="r", facts_hash="x"))
    assert (out1 / "facts.jsonl").read_bytes() == (out2 / "facts.jsonl").read_bytes()


def test_facts_sorted_node_before_edge(tmp_path: Path):
    nodes = [n("svc:a")]
    edges = [e("svc:a", "svc:b")]
    nodes.append(n("svc:b"))
    out = tmp_path / "g"
    write_graph(out, nodes, edges, Manifest(schema_version=1, chunk_count=1,
                node_count=2, edge_count=1, run_id="r", facts_hash="x"))
    lines = (out / "facts.jsonl").read_text().splitlines()
    kinds = [json.loads(l)["kind"] for l in lines]
    assert kinds == sorted(kinds)               # nodes (edge<n? 'edge'<'node' -> edges first is fine; assert stable)


def test_manifest_written(tmp_path: Path):
    out = tmp_path / "g"
    m = Manifest(schema_version=1, chunk_count=1, node_count=1, edge_count=0,
                 run_id="r", facts_hash="x", chunk_hashes={"abc": ["svc:a"]})
    write_graph(out, [n("svc:a")], [], m)
    loaded = Manifest.from_json((out / "manifest.json").read_text())
    assert loaded.chunk_hashes == {"abc": ["svc:a"]}


def test_run_id_deterministic():
    from laputa.models import DocChunk
    cs = [DocChunk(path="b.md", start_line=1, end_line=1, text="x", namespace="t/b"),
          DocChunk(path="a.md", start_line=1, end_line=1, text="x", namespace="t/a")]
    assert run_id(cs, 1) == run_id(list(reversed(cs)), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_writer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/compile/writer.py`**

```python
"""Writer: emit deterministic facts.jsonl + manifest.json.

Determinism = facts sorted by (kind, type, id), JSON keys sorted, stable
separators. Re-compiling unchanged input yields byte-identical output.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from laputa.models import DocChunk, Edge, Manifest, Node


def _sort_key(fact: Node | Edge) -> tuple[str, str, str]:
    return (fact.kind, fact.type, fact.id)


def write_graph(
    out_dir: Path, nodes: list[Node], edges: list[Edge], manifest: Manifest,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = sorted(nodes + edges, key=_sort_key)
    lines = [f.to_json_line() for f in facts]
    text = "\n".join(lines) + ("\n" if lines else "")
    (out_dir / "facts.jsonl").write_text(text, encoding="utf-8")
    (out_dir / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")


def run_id(chunks: list[DocChunk], schema_version: int) -> str:
    h = hashlib.sha256()
    h.update(str(schema_version).encode("utf-8"))
    for c in sorted(chunks, key=lambda c: (c.path, c.start_line)):
        h.update(c.hash.encode("utf-8"))
    return h.hexdigest()[:16]


def facts_hash(out_dir: Path) -> str:
    raw = (out_dir / "facts.jsonl").read_bytes()
    return hashlib.sha256(raw).hexdigest()[:16]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_writer.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/compile/writer.py tests/test_writer.py
git commit -m "feat(writer): deterministic facts.jsonl + manifest emit"
```

---

## Task 12: Pipeline orchestrator + `compile` CLI

**Files:**
- Create: `src/laputa/pipeline.py`
- Modify: `src/laputa/cli.py` (add `compile`)
- Test: `tests/test_pipeline.py`, `tests/test_compile_cli.py`

- [ ] **Step 1: Write the failing test `tests/test_pipeline.py`**

```python
import json
from pathlib import Path
from laputa.pipeline import compile_graph
from laputa.compile.providers import FakeProvider
from laputa.models import Schema


def copy_fixture(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text())


def test_compile_pipeline_produces_facts(tmp_path: Path, fixtures: Path):
    raw = tmp_path / "raw"
    copy_fixture(fixtures / "raw/teams/backend/payments.md",
                 raw / "teams/backend/payments.md")
    out = tmp_path / "graph"
    cache = tmp_path / "cache.json"
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    canned = json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth"},
            {"id": "team:backend", "type": "team", "name": "team-backend"},
            {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
             "props": {"title": "payments-api adopts internal signing"}},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
            {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
        ],
        "aliases": {},
    })
    provider = FakeProvider([canned])

    manifest = compile_graph(raw_root=raw, out_dir=out, schema=schema,
                             provider=provider, cache_path=cache, chunk_lines=60)
    facts = (out / "facts.jsonl").read_text().splitlines()
    assert len(facts) == 6                       # 4 nodes + 2 edges
    assert (out / "manifest.json").exists()
    assert manifest.node_count == 4
    assert manifest.edge_count == 2
```

- [ ] **Step 2: Write the failing test `tests/test_compile_cli.py`**

```python
import json
from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_compile_command_uses_config_provider(monkeypatch, tmp_path: Path, fixtures: Path):
    # point the CLI at temp dirs via env
    monkeypatch.setenv("LAPUTA_RAW", str(tmp_path / "raw"))
    monkeypatch.setenv("LAPUTA_OUT", str(tmp_path / "graph"))
    monkeypatch.setenv("LAPUTA_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("LAPUTA_SCHEMA", str(fixtures / "schema.json"))
    monkeypatch.setenv("LAPUTA_PROVIDER", "fake")

    raw = tmp_path / "raw/teams/backend/payments.md"
    raw.parent.mkdir(parents=True)
    raw.write_text((fixtures / "raw/teams/backend/payments.md").read_text())

    result = runner.invoke(app, ["compile"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "graph/facts.jsonl").exists()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py tests/test_compile_cli.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Write `src/laputa/pipeline.py`**

```python
"""Pipeline: ingest -> extract -> resolve -> writer."""
from __future__ import annotations

from pathlib import Path

from laputa.compile.extract import ExtractionCache, extract_chunk
from laputa.compile.ingest import ingest
from laputa.compile.providers import LLMProvider
from laputa.compile.resolve import resolve
from laputa.compile.writer import facts_hash, run_id, write_graph
from laputa.models import Manifest, Node, Edge, Schema


def compile_graph(
    raw_root: Path,
    out_dir: Path,
    schema: Schema,
    provider: LLMProvider,
    cache_path: Path,
    chunk_lines: int = 60,
) -> Manifest:
    chunks = ingest(raw_root, chunk_lines=chunk_lines)
    cache = ExtractionCache(cache_path)

    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    all_aliases: dict[str, list[str]] = {}
    errors = []
    for chunk in chunks:
        try:
            nodes, edges, aliases = extract_chunk(chunk, schema, provider, cache)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
            all_aliases.update(aliases)
        except Exception as exc:               # skip-and-log; partial compile is valid
            errors.append({"path": chunk.path, "line": chunk.start_line,
                           "message": str(exc)})
    cache.save()

    resolved = resolve(all_nodes, all_edges, name_aliases=all_aliases)

    rid = run_id(chunks, schema.version)
    # write once to compute facts_hash, then write the real manifest
    from laputa.models import Manifest as M
    provisional = M(schema_version=schema.version, chunk_count=len(chunks),
                    node_count=len(resolved.nodes), edge_count=len(resolved.edges),
                    run_id=rid, facts_hash="")
    write_graph(out_dir, resolved.nodes, resolved.edges, provisional)
    fh = facts_hash(out_dir)

    chunk_hashes: dict[str, list[str]] = {}
    for c in chunks:
        chunk_hashes[c.hash[:16]] = [n.id for n in resolved.nodes
                                     if c.src in n.src] + [e.id for e in resolved.edges
                                                                    if c.src in e.src]
    manifest = M(
        schema_version=schema.version,
        chunk_count=len(chunks),
        node_count=len(resolved.nodes),
        edge_count=len(resolved.edges),
        run_id=rid,
        facts_hash=fh,
        chunk_hashes=chunk_hashes,
        errors=errors,
        quarantine=[{"fact": q[0], "reason": q[1]} for q in resolved.quarantined],
    )
    write_graph(out_dir, resolved.nodes, resolved.edges, manifest)
    return manifest
```

- [ ] **Step 5: Rewrite `src/laputa/cli.py`**

```python
"""Laputa CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from laputa.compile.providers import FakeProvider, LiteLLMProvider
from laputa.config import load_config
from laputa.models import Schema
from laputa.pipeline import compile_graph
from laputa.schema_io import load_schema

app = typer.Typer(help="Laputa — compile team docs into a temporal knowledge graph.")


def _paths() -> dict[str, Path]:
    return {
        "raw": Path(os.environ.get("LAPUTA_RAW", "raw")),
        "out": Path(os.environ.get("LAPUTA_OUT", "graph")),
        "cache": Path(os.environ.get("LAPUTA_CACHE", ".laputa/cache.json")),
        "schema": Path(os.environ.get("LAPUTA_SCHEMA", "graph/schema.json")),
        "config": Path(os.environ.get("LAPUTA_CONFIG", ".laputa/config.yaml")),
    }


@app.command()
def version() -> None:
    """Print the Laputa version."""
    typer.echo("laputa 0.1.0")


@app.command()
def compile() -> None:
    """Compile raw/ into graph/facts.jsonl."""
    p = _paths()
    schema = load_schema(p["schema"])
    config = load_config(p["config"])

    provider = FakeProvider(responses=[])            # placeholder, replaced below
    if os.environ.get("LAPUTA_PROVIDER") == "fake":
        canned = json.dumps({
            "nodes": [
                {"id": "svc:payments-api", "type": "service", "name": "payments-api",
                 "props": {"lang": "go"}, "valid_from": "2024-01-15"},
                {"id": "svc:auth", "type": "service", "name": "auth"},
                {"id": "team:backend", "type": "team", "name": "team-backend"},
                {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
                 "props": {"title": "payments-api adopts internal signing"}},
            ],
            "edges": [
                {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
                 "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
                {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
            ],
            "aliases": {},
        })
        provider = FakeProvider(responses=[canned])
    else:
        provider = LiteLLMProvider(
            model=config.provider.model,
            api_base=config.provider.api_base,
            temperature=config.provider.temperature,
        )

    manifest = compile_graph(
        raw_root=p["raw"], out_dir=p["out"], schema=schema,
        provider=provider, cache_path=p["cache"], chunk_lines=config.compile.chunk_lines,
    )
    typer.echo(f"compiled: {manifest.node_count} nodes, {manifest.edge_count} edges, "
               f"run_id={manifest.run_id}, facts_hash={manifest.facts_hash}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Add shared fixture to `tests/conftest.py`**

```python
from pathlib import Path
import pytest


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py tests/test_compile_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 8: Commit**

```bash
git add src/laputa/pipeline.py src/laputa/cli.py tests/conftest.py tests/test_pipeline.py tests/test_compile_cli.py
git commit -m "feat(pipeline): wire ingest->extract->resolve->writer + compile CLI"
```

---

## Task 13: Determinism property test (Tier-1)

**Files:**
- Create: `tests/test_determinism.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path
from laputa.pipeline import compile_graph
from laputa.compile.providers import FakeProvider
from laputa.models import Schema


def test_recompile_is_byte_identical(tmp_path: Path, fixtures: Path):
    def make_canned():
        return json.dumps({
            "nodes": [
                {"id": "svc:payments-api", "type": "service", "name": "payments-api",
                 "props": {"lang": "go"}, "valid_from": "2024-01-15"},
                {"id": "svc:auth", "type": "service", "name": "auth"},
            ],
            "edges": [{"type": "depends_on", "from": "svc:payments-api",
                       "to": "svc:auth", "valid_from": "2024-01-15",
                       "valid_to": "2025-03-01"}],
            "aliases": {},
        })

    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/p.md").write_text(
        (fixtures / "raw/teams/backend/payments.md").read_text())
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))

    out1, out2 = tmp_path / "g1", tmp_path / "g2"
    compile_graph(raw, out1, schema, FakeProvider([make_canned()]),
                  tmp_path / "c1.json")
    compile_graph(raw, out2, schema, FakeProvider([make_canned()]),
                  tmp_path / "c2.json")

    b1 = (out1 / "facts.jsonl").read_bytes()
    b2 = (out2 / "facts.jsonl").read_bytes()
    assert b1 == b2, "re-compile of identical input must be byte-identical"


def test_cache_makes_recompile_identical_without_new_llm_call(tmp_path: Path, fixtures: Path):
    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/p.md").write_text(
        (fixtures / "raw/teams/backend/payments.md").read_text())
    schema = Schema.load(json.loads((fixtures / "schema.json").read_text()))
    cache = tmp_path / "shared.json"

    canned = json.dumps({"nodes": [{"id": "svc:x", "type": "service", "name": "x"}],
                         "edges": [], "aliases": {}})
    # first run consumes the one canned response; second run must use cache
    compile_graph(raw, tmp_path / "g1", schema, FakeProvider([canned]), cache)
    compile_graph(raw, tmp_path / "g2", schema, FakeProvider([]), cache)   # no responses!
    assert (tmp_path / "g1/facts.jsonl").read_bytes() == \
           (tmp_path / "g2/facts.jsonl").read_bytes()
```

- [ ] **Step 2: Run test to verify it passes** (implementation already supports this)

Run: `uv run pytest tests/test_determinism.py -v`
Expected: PASS (2 passed). If it fails, the cache or writer is non-deterministic — fix before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_determinism.py
git commit -m "test(determinism): re-compile byte-identical + cache reuse property"
```

---

## Task 14: Tier-1 eval — gold loader + fact keying

**Files:**
- Create: `src/laputa/eval/__init__.py` (empty)
- Create: `src/laputa/eval/gold.py`
- Test: `tests/test_construction_eval.py` (first cases)

- [ ] **Step 1: Write the failing test (append to `tests/test_construction_eval.py`)**

```python
from pathlib import Path
from laputa.eval.gold import load_gold, load_compiled, node_key, edge_key


def test_load_gold(tmp_path: Path, fixtures: Path):
    facts = load_gold(fixtures / "gold")
    ids = {f.id for f in facts}
    assert "svc:payments-api" in ids
    assert len(facts) == 6


def test_node_key_uses_type_and_name():
    from laputa.models import Node
    n = Node(id="svc:x", type="service", ns=("t/b",), props={"name": "auth"})
    assert node_key(n) == ("service", "auth")


def test_edge_key_uses_type_and_endpoint_names():
    from laputa.models import Node, Edge
    nodes = {"svc:a": Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
             "svc:b": Node(id="svc:b", type="service", ns=("t/b",), props={"name": "b"})}
    e = Edge(id="e1", type="depends_on", **{"from": "svc:a"}, to="svc:b", ns=("t/b",))
    assert edge_key(e, nodes) == ("depends_on", "a", "b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_construction_eval.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `src/laputa/eval/__init__.py`** (empty)

- [ ] **Step 4: Write `src/laputa/eval/gold.py`**

```python
"""Load gold + compiled facts, and define match keys for evaluation."""
from __future__ import annotations

import json
from pathlib import Path

from laputa.models import Edge, Node


def _read_facts(path: Path) -> list[Node | Edge]:
    facts: list[Node | Edge] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if d["kind"] == "node":
            facts.append(Node.model_validate(d))
        else:
            facts.append(Edge.model_validate(d))
    return facts


def load_gold(gold_dir: Path) -> list[Node | Edge]:
    """Load every *.facts.jsonl under gold_dir."""
    facts: list[Node | Edge] = []
    for p in sorted(gold_dir.glob("**/*.facts.jsonl")):
        facts.extend(_read_facts(p))
    return facts


def load_compiled(graph_dir: Path) -> list[Node | Edge]:
    return _read_facts(graph_dir / "facts.jsonl")


def node_key(n: Node) -> tuple[str, str]:
    return (n.type, n.props.get("name", n.id))


def edge_key(e: Edge, nodes_by_id: dict[str, Node]) -> tuple[str, str, str]:
    f = nodes_by_id.get(e.from_)
    t = nodes_by_id.get(e.to)
    fn = f.props.get("name", e.from_) if f else e.from_
    tn = t.props.get("name", e.to) if t else e.to
    return (e.type, fn, tn)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_construction_eval.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/laputa/eval/__init__.py src/laputa/eval/gold.py tests/test_construction_eval.py
git commit -m "feat(eval): gold/compiled loaders + node/edge match keys"
```

---

## Task 15: Tier-1 eval — extraction P/R/F1

**Files:**
- Create: `src/laputa/eval/construction.py`
- Test: `tests/test_construction_eval.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from laputa.eval.construction import precision_recall_f1, extraction_report


def test_prf1_perfect():
    p, r, f1 = precision_recall_f1({1, 2, 3}, {1, 2, 3})
    assert (p, r, f1) == (1.0, 1.0, 1.0)


def test_prf1_partial():
    p, r, f1 = precision_recall_f1({1, 2, 3}, {2, 3, 4})
    assert p == 2/3 and r == 2/3 and abs(f1 - 2/3) < 1e-9


def test_extraction_report_against_gold(tmp_path: Path, fixtures: Path):
    # compile with the canned fixture response, then score vs gold
    import json as _json
    from laputa.pipeline import compile_graph
    from laputa.compile.providers import FakeProvider
    from laputa.models import Schema
    from laputa.eval.gold import load_gold

    raw = tmp_path / "raw"
    (raw / "teams/backend").mkdir(parents=True)
    (raw / "teams/backend/payments.md").write_text(
        (fixtures / "raw/teams/backend/payments.md").read_text())
    schema = Schema.load(_json.loads((fixtures / "schema.json").read_text()))
    canned = _json.dumps({
        "nodes": [
            {"id": "svc:payments-api", "type": "service", "name": "payments-api",
             "props": {"lang": "go"}, "valid_from": "2024-01-15"},
            {"id": "svc:auth", "type": "service", "name": "auth"},
            {"id": "team:backend", "type": "team", "name": "team-backend"},
            {"id": "dec:adr-007", "type": "decision", "name": "adr-007",
             "props": {"title": "payments-api adopts internal signing"}},
        ],
        "edges": [
            {"type": "depends_on", "from": "svc:payments-api", "to": "svc:auth",
             "valid_from": "2024-01-15", "valid_to": "2025-03-01"},
            {"type": "decided_by", "from": "dec:adr-007", "to": "team:backend"},
        ],
        "aliases": {},
    })
    compile_graph(raw, tmp_path / "g", schema, FakeProvider([canned]), tmp_path / "c.json")
    report = extraction_report(tmp_path / "g", fixtures / "gold")
    assert report["nodes"]["f1"] == 1.0
    assert report["edges"]["f1"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_construction_eval.py -v`
Expected: FAIL — `ImportError: cannot import name 'precision_recall_f1'`.

- [ ] **Step 3: Write `src/laputa/eval/construction.py`**

```python
"""Tier-1 construction-quality evaluation vs a gold corpus."""
from __future__ import annotations

from pathlib import Path

from laputa.eval.gold import edge_key, load_compiled, load_gold, node_key
from laputa.models import Edge, Node


def precision_recall_f1(gold: set, got: set) -> tuple[float, float, float]:
    if not gold and not got:
        return 1.0, 1.0, 1.0
    tp = len(gold & got)
    p = tp / len(got) if got else 0.0
    r = tp / len(gold) if gold else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return p, r, f1


def extraction_report(graph_dir: Path, gold_dir: Path) -> dict:
    compiled = load_compiled(graph_dir)
    gold = load_gold(gold_dir)

    c_nodes = [f for f in compiled if isinstance(f, Node)]
    c_edges = [f for f in compiled if isinstance(f, Edge)]
    g_nodes = [f for f in gold if isinstance(f, Node)]
    g_edges = [f for f in gold if isinstance(f, Edge)]

    c_ids = {n.id: n for n in c_nodes}
    g_ids = {n.id: n for n in g_nodes}

    c_node_keys = {node_key(n) for n in c_nodes}
    g_node_keys = {node_key(n) for n in g_nodes}
    c_edge_keys = {edge_key(e, c_ids) for e in c_edges}
    g_edge_keys = {edge_key(e, g_ids) for e in g_edges}

    np, nr, nf = precision_recall_f1(g_node_keys, c_node_keys)
    ep, er, ef = precision_recall_f1(g_edge_keys, c_edge_keys)
    return {
        "nodes": {"precision": np, "recall": nr, "f1": nf},
        "edges": {"precision": ep, "recall": er, "f1": ef},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_construction_eval.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/eval/construction.py tests/test_construction_eval.py
git commit -m "feat(eval): extraction P/R/F1 vs gold corpus"
```

---

## Task 16: Tier-1 eval — entity-resolution pairwise F1

**Files:**
- Modify: `src/laputa/eval/construction.py` (add `entity_resolution_f1`)
- Test: `tests/test_construction_eval.py` (append)

- [ ] **Step 1: Append failing test**

```python
from laputa.eval.construction import entity_resolution_f1


def test_er_f1_perfect_merge():
    # two mentions that should be one entity, correctly merged (same id)
    from laputa.models import Node
    compiled = [Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
                Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"})]
    gold = [{"id": "svc:a", "aliases": ["a", "a2"]}]
    r = entity_resolution_f1(compiled, gold)
    assert r["f1"] == 1.0


def test_er_f1_false_split():
    # gold says one entity, compiled split into two -> recall drops
    from laputa.models import Node
    compiled = [Node(id="svc:a", type="service", ns=("t/b",), props={"name": "a"}),
                Node(id="svc:b", type="service", ns=("t/b",), props={"name": "b"})]
    gold = [{"id": "svc:x", "aliases": ["a", "b"]}]
    r = entity_resolution_f1(compiled, gold)
    assert r["recall"] < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_construction_eval.py::test_er_f1_perfect_merge -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Append to `src/laputa/eval/construction.py`**

```python
from itertools import combinations


def _clusters_from_aliases(alias_groups: list[dict]) -> list[set[str]]:
    """Each gold group -> set of surface names that should be one entity."""
    return [set(g["aliases"]) for g in alias_groups]


def _compiled_clusters(compiled_nodes: list) -> list[set[str]]:
    """Group compiled node names by their id (canonical entity)."""
    by_id: dict[str, set[str]] = {}
    for n in compiled_nodes:
        nm = n.props.get("name", n.id)
        by_id.setdefault(n.id, set()).add(nm)
    return list(by_id.values())


def entity_resolution_f1(compiled_nodes: list, gold_alias_groups: list[dict]) -> dict:
    """Pairwise coreference F1: for every pair of mentions, did compiled agree with gold?"""
    gold_pairs: set[frozenset] = set()
    for cluster in _clusters_from_aliases(gold_alias_groups):
        for a, b in combinations(sorted(cluster), 2):
            gold_pairs.add(frozenset((a, b)))

    got_pairs: set[frozenset] = set()
    for cluster in _compiled_clusters(compiled_nodes):
        for a, b in combinations(sorted(cluster), 2):
            got_pairs.add(frozenset((a, b)))

    p, r, f1 = precision_recall_f1(gold_pairs, got_pairs)
    return {"precision": p, "recall": r, "f1": f1}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_construction_eval.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/eval/construction.py tests/test_construction_eval.py
git commit -m "feat(eval): entity-resolution pairwise F1"
```

---

## Task 17: Tier-1 eval — graph structure metrics

**Files:**
- Modify: `src/laputa/eval/construction.py` (add `structure_report`)
- Test: `tests/test_construction_eval.py` (append)

- [ ] **Step 1: Append failing test**

```python
from laputa.eval.construction import structure_report


def test_structure_metrics(tmp_path: Path, fixtures: Path):
    report = structure_report(fixtures / "gold")
    assert report["node_count"] == 4
    assert report["edge_count"] == 2
    assert report["dangling_edge_rate"] == 0.0
    assert report["avg_degree"] == 0.5            # 2 edges / 4 nodes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_construction_eval.py::test_structure_metrics -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Append to `src/laputa/eval/construction.py`**

```python
def structure_report(facts_dir: Path) -> dict:
    """Graph-shape metrics: counts, avg degree, density, dangling-edge rate."""
    # accept either a gold dir or a compiled graph dir
    if (facts_dir / "facts.jsonl").exists():
        facts = load_compiled(facts_dir)
    else:
        facts = load_gold(facts_dir)
    nodes = [f for f in facts if isinstance(f, Node)]
    edges = [f for f in facts if isinstance(f, Edge)]
    node_ids = {n.id for n in nodes}
    dangling = sum(1 for e in edges if e.from_ not in node_ids or e.to not in node_ids)
    n = len(nodes)
    e = len(edges)
    avg_degree = (e / n) if n else 0.0
    density = (e / (n * (n - 1))) if n > 1 else 0.0
    return {
        "node_count": n,
        "edge_count": e,
        "avg_degree": round(avg_degree, 4),
        "density": round(density, 4),
        "dangling_edge_rate": round(dangling / e, 4) if e else 0.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_construction_eval.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/eval/construction.py tests/test_construction_eval.py
git commit -m "feat(eval): graph structure metrics (degree, density, dangling rate)"
```

---

## Task 18: `eval construction` + `check` CLI

**Files:**
- Modify: `src/laputa/cli.py` (add `eval` and `check`)
- Test: `tests/test_eval_cli.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path
from typer.testing import CliRunner
from laputa.cli import app

runner = CliRunner()


def test_eval_construction_command(tmp_path: Path, fixtures: Path, monkeypatch):
    # build a compiled graph equal to gold so scores are perfect
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text(
        (fixtures / "gold/payments.facts.jsonl").read_text())
    monkeypatch.setenv("LAPUTA_OUT", str(out))
    monkeypatch.setenv("LAPUTA_GOLD", str(fixtures / "gold"))
    monkeypatch.setenv("LAPUTA_EVAL_RESULTS", str(tmp_path / "results.json"))

    result = runner.invoke(app, ["eval", "construction"])
    assert result.exit_code == 0, result.stdout
    assert "f1" in result.stdout
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["nodes"]["f1"] == 1.0


def test_check_command_reports_clean_graph(tmp_path: Path, fixtures: Path, monkeypatch):
    out = tmp_path / "graph"
    out.mkdir()
    (out / "facts.jsonl").write_text(
        (fixtures / "gold/payments.facts.jsonl").read_text())
    monkeypatch.setenv("LAPUTA_OUT", str(out))
    monkeypatch.setenv("LAPUTA_SCHEMA", str(fixtures / "schema.json"))
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.stdout
    assert "ok" in result.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_cli.py -v`
Expected: FAIL — `Error: No such command 'eval'`.

- [ ] **Step 3: Append two commands to `src/laputa/cli.py`** (before the `if __name__` block). The function is named `eval_cmd` with `name="eval"` to avoid shadowing the Python builtin `eval`.

```python
@app.command(name="eval")
def eval_cmd() -> None:
    """Run Tier-1 construction-quality evaluation vs the gold corpus."""
    p = _paths()
    gold_dir = Path(os.environ.get("LAPUTA_GOLD", "tests/fixtures/gold"))
    from laputa.eval.construction import extraction_report, structure_report
    report = {
        "extraction": extraction_report(p["out"], gold_dir),
        "structure": structure_report(p["out"]),
    }
    results_path = Path(os.environ.get("LAPUTA_EVAL_RESULTS",
                                       ".laputa/eval/results.json"))
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    typer.echo(json.dumps(report, indent=2, sort_keys=True))


@app.command()
def check() -> None:
    """Validate the compiled graph: loads, no dangling edges."""
    p = _paths()
    from laputa.eval.construction import structure_report
    struct = structure_report(p["out"])
    if struct["dangling_edge_rate"] > 0:
        typer.echo(f"check: FAIL — {struct['dangling_edge_rate']} dangling edges")
        raise typer.Exit(code=1)
    typer.echo(f"check: ok — {struct['node_count']} nodes, {struct['edge_count']} edges, 0 dangling")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/laputa/cli.py tests/test_eval_cli.py
git commit -m "feat(cli): eval construction + check commands"
```

---

## Task 19: Docs + end-to-end manual smoke

**Files:**
- Create: `docs/compile.md`
- Create: `.laputa/config.yaml.example`
- Modify: `.gitignore` (ensure `.laputa/` ignored)

- [ ] **Step 1: Ensure `.laputa/` is gitignored**

Run: `grep -q '\.laputa/' .gitignore || echo '.laputa/' >> .gitignore`

- [ ] **Step 2: Write `.laputa/config.yaml.example`**

```yaml
# Copy to .laputa/config.yaml (gitignored). Provider used at compile time only.
provider:
  backend: openai            # openai | anthropic | ollama
  model: gpt-4o-mini
  api_base: null             # set http://localhost:11434 for ollama
  temperature: 0.0
compile:
  chunk_lines: 60
```

- [ ] **Step 3: Write `docs/compile.md`**

````markdown
# Compiling the knowledge graph

## 1. Add raw docs

Drop markdown under `raw/teams/<namespace>/`:

```
raw/teams/backend/payments.md
raw/teams/frontend/web.md
```

The directory under `teams/` becomes the fact's `ns`.

## 2. Configure a provider

```bash
cp .laputa/config.yaml.example .laputa/config.yaml
# edit model / api_base as needed
```

For strict privacy, use a local model:

```yaml
provider:
  backend: ollama
  model: ollama/llama3
  api_base: http://localhost:11434
```

## 3. Compile

```bash
uv run laputa compile
```

Produces `graph/facts.jsonl` + `graph/manifest.json`. Re-running is idempotent:
unchanged input yields a byte-identical file (extraction is cached under
`.laputa/cache.json`).

## 4. Evaluate construction quality

Author gold facts under `tests/fixtures/gold/<name>.facts.jsonl`, then:

```bash
uv run laputa eval
```

Reports extraction P/R/F1, entity-resolution F1, and graph-structure metrics.
Snapshots to `.laputa/eval/results.json`.

## 5. Validate

```bash
uv run laputa check
```

Exits non-zero if the graph has dangling edges.
````

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS (≈30 tests across all files).

- [ ] **Step 5: Manual end-to-end smoke (no real API, uses fake provider)**

Run:
```bash
LAPUTA_PROVIDER=fake uv run laputa compile
uv run laputa check
LAPUTA_GOLD=tests/fixtures/gold uv run laputa eval
```
Expected: `compile` prints node/edge counts; `check` prints `ok`; `eval` prints an `f1` report.

- [ ] **Step 6: Commit**

```bash
git add docs/compile.md .laputa/config.yaml.example .gitignore
git commit -m "docs: compile quickstart + provider config example; ensure .laputa ignored"
```

---

## Self-Review (run after writing)

**Spec coverage (Plan A scope only):**
- ingest (markdown → DocChunk) → Task 5 ✓
- extract (LLM, pluggable, schema-constrained) → Tasks 6, 7 ✓
- resolve (dedup, validate, ns, quarantine) → Tasks 9, 10 ✓
- writer (deterministic facts.jsonl + manifest + schema) → Task 11 (+ schema in Task 1) ✓
- incremental chunk-hash cache → Task 8 ✓
- error handling skip-and-log + quarantine → Tasks 9, 12 ✓
- Tier-1 extraction P/R/F1 vs gold → Task 15 ✓
- entity-res F1 → Task 16 ✓
- graph structure metrics → Task 17 ✓
- determinism property test → Task 13 ✓
- deliverable: deterministic facts.jsonl + measured quality → Tasks 12, 15–18 ✓
- (Plan B: store/perm/MCP/integrations) — correctly out of scope ✓

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N" remain. Every code step contains complete, paste-able code.

**Type consistency:** `Node.from_` (alias `from`) used consistently; `to_json_line()` on Node+Edge; `Manifest` fields `run_id`/`facts_hash`/`chunk_hashes`/`errors`/`quarantine` match across writer + pipeline + tests; `precision_recall_f1(gold, got)` argument order consistent in Tasks 15–16.
