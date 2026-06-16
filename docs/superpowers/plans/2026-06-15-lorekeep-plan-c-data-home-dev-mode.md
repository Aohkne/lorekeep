# Lorekeep Plan C — Data Home + Dev Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the Lorekeep tool from its data so `uvx lorekeep` installs clean (data in an XDG home by default) while keeping convenient local-dev (repo co-located data, zero migration).

**Architecture:** A new `paths.py` provides `resolve_paths()` with 4-tier precedence: (1) explicit per-path env overrides `LOREKEEP_RAW/OUT/CACHE/SCHEMA/CONFIG` (tests + power users), (2) `LOREKEEP_HOME` → unified `<home>/{config.yaml,schema.json,raw,graph,cache.json}`, (3) dev mode (`.lorekeep/` or `raw/` in CWD, or `LOREKEEP_DEV=1`) → current CWD layout unchanged, (4) default → XDG via `platformdirs`. CLI commands call `resolve_paths()` instead of the old `_paths()`; the returned dict shape is identical so command bodies don't change. A new `lorekeep init` bootstraps the home with default config + schema. `mcp add` already emits a portable `.mcp.json` when `install_source` is `pypi`/`local`.

**Tech Stack:** Python 3.11+, platformdirs (new dep), typer, pydantic, uv.

**Spec:** `docs/superpowers/specs/2026-06-14-lorekeep-temporal-kg-mcp-design.md`. **Prior plans:** A (compile) + B (serve) merged (95 tests green).

**Current `_paths()` (cli.py:25-32)** reads `LOREKEEP_*` env with CWD defaults — this is exactly dev mode. The refactor generalizes the default (dev/home/xdg) while preserving the env-override tier.

---

## File Structure

```
src/lorekeep/
├── paths.py          # NEW: resolve_paths() 4-tier precedence (pure, testable)
├── defaults.py       # NEW: DEFAULT_SCHEMA + DEFAULT_CONFIG_YAML for `init`
├── cli.py            # MODIFY: drop _paths(), use resolve_paths(); add `init` command
└── (config.py, others unchanged)
pyproject.toml        # MODIFY: add platformdirs
docs/serve.md         # MODIFY: dev/XDG/home/init flow + portable .mcp.json
docs/compile.md       # MODIFY: data-home note
.lorekeep/config.yaml.example  # MODIFY: install_source note (pypi for portable)
tests/
├── test_paths.py     # NEW
├── test_defaults.py  # NEW
├── test_init_cli.py  # NEW
└── (all existing tests unchanged — they use LOREKEEP_* env = tier 1)
```

**Boundaries:** `paths.py` is pure path resolution (no I/O, no side effects) — fully testable. `defaults.py` holds bootstrap constants. `cli.py` stays thin. Existing commands change only their path-source call (same dict shape).

---

## Task 1: Add `platformdirs` dependency

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Add platformdirs to the `dependencies` list in `pyproject.toml`**

Insert `"platformdirs>=4.0",` into the `dependencies` array (keep existing entries). Full list after edit:

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
  "platformdirs>=4.0",
]
```

- [ ] **Step 2: Sync and verify import**

Run:
```bash
uv sync
uv run python -c "import platformdirs; print(platformdirs.user_config_dir('lorekeep'), platformdirs.user_data_dir('lorekeep'))"
```
Expected: prints two paths (e.g. `~/.config/lorekeep ~/.local/share/lorekeep`), no ImportError.

- [ ] **Step 3: Confirm suite still green**

Run: `uv run pytest -q`
Expected: 95 passed.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add platformdirs for XDG data/config dirs"
```

---

## Task 2: `paths.py` — `resolve_paths()` (4-tier precedence)

**Files:** Create `src/lorekeep/paths.py`, Test `tests/test_paths.py`

- [ ] **Step 1: Write the failing test `tests/test_paths.py`**

```python
from pathlib import Path
from lorekeep.paths import resolve_paths


def test_dev_mode_via_lorekeep_marker(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()
    p = resolve_paths()
    assert p["config"] == tmp_path / ".lorekeep" / "config.yaml"
    assert p["cache"] == tmp_path / ".lorekeep" / "cache.json"
    assert p["raw"] == tmp_path / "raw"
    assert p["out"] == tmp_path / "graph"
    assert p["schema"] == tmp_path / "graph" / "schema.json"


def test_dev_mode_via_raw_marker(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "raw").mkdir()
    assert resolve_paths()["raw"] == tmp_path / "raw"


def test_lorekeep_home_overrides_dev(tmp_path: Path, monkeypatch):
    # even inside a dev-marked dir, explicit LOREKEEP_HOME wins
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()
    home = tmp_path / "myhome"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    p = resolve_paths()
    assert p["config"] == home / "config.yaml"
    assert p["raw"] == home / "raw"
    assert p["schema"] == home / "schema.json"


def test_xdg_default(tmp_path: Path, monkeypatch):
    # no dev marker, no LOREKEEP_HOME -> XDG (redirect via XDG_*_HOME for determinism)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.delenv("LOREKEEP_HOME", raising=False)
    monkeypatch.delenv("LOREKEEP_DEV", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    p = resolve_paths()
    assert p["config"] == tmp_path / "xdg-config" / "lorekeep" / "config.yaml"
    assert p["raw"] == tmp_path / "xdg-data" / "lorekeep" / "raw"
    assert p["schema"] == tmp_path / "xdg-data" / "lorekeep" / "schema.json"


def test_explicit_env_overrides_everything(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lorekeep").mkdir()                 # dev mode active
    monkeypatch.setenv("LOREKEEP_RAW", "/custom/raw")
    monkeypatch.setenv("LOREKEEP_OUT", "/custom/graph")
    monkeypatch.setenv("LOREKEEP_CONFIG", "/custom/config.yaml")
    p = resolve_paths()
    assert p["raw"] == Path("/custom/raw")
    assert p["out"] == Path("/custom/graph")
    assert p["config"] == Path("/custom/config.yaml")
    # non-overridden paths still dev
    assert p["schema"] == tmp_path / "graph" / "schema.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lorekeep.paths'`).

- [ ] **Step 3: Write `src/lorekeep/paths.py`**

```python
"""Path resolution with 4-tier precedence (high -> low).

1. explicit per-path env (LOREKEEP_RAW/OUT/CACHE/SCHEMA/CONFIG) - tests + power users
2. LOREKEEP_HOME -> unified <home>/{config.yaml,schema.json,raw,graph,cache.json}
3. dev mode (.lorekeep/ or raw/ in CWD, or LOREKEEP_DEV=1) -> current CWD layout
4. default -> XDG (platformdirs): config + data dirs

Pure: no I/O, no side effects. Fully testable.
"""
from __future__ import annotations

import os
from pathlib import Path


def _dev_marker(cwd: Path) -> bool:
    return (cwd / ".lorekeep").is_dir() or (cwd / "raw").is_dir()


def resolve_paths() -> dict[str, Path]:
    cwd = Path.cwd()
    home = os.environ.get("LOREKEEP_HOME")
    dev = os.environ.get("LOREKEEP_DEV") == "1" or _dev_marker(cwd)

    if home:
        base = Path(home).expanduser()
        config = base / "config.yaml"
        cache = base / "cache.json"
        raw = base / "raw"
        out = base / "graph"
        schema = base / "schema.json"
    elif dev:
        config = cwd / ".lorekeep" / "config.yaml"
        cache = cwd / ".lorekeep" / "cache.json"
        raw = cwd / "raw"
        out = cwd / "graph"
        schema = cwd / "graph" / "schema.json"
    else:
        from platformdirs import user_config_dir, user_data_dir
        cfg_dir = Path(user_config_dir("lorekeep"))
        data_dir = Path(user_data_dir("lorekeep"))
        config = cfg_dir / "config.yaml"
        cache = data_dir / "cache.json"
        raw = data_dir / "raw"
        out = data_dir / "graph"
        schema = data_dir / "schema.json"

    def override(env_name: str, current: Path) -> Path:
        v = os.environ.get(env_name)
        return Path(v).expanduser() if v else current

    return {
        "raw": override("LOREKEEP_RAW", raw),
        "out": override("LOREKEEP_OUT", out),
        "cache": override("LOREKEEP_CACHE", cache),
        "schema": override("LOREKEEP_SCHEMA", schema),
        "config": override("LOREKEEP_CONFIG", config),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/lorekeep/paths.py tests/test_paths.py
git commit -m "feat(paths): resolve_paths() with dev/home/XDG 4-tier precedence"
```

---

## Task 3: `cli.py` — use `resolve_paths()` (replace `_paths()`)

**Files:** Modify `src/lorekeep/cli.py`

- [ ] **Step 1: Add the import** — at the top of `src/lorekeep/cli.py`, add after the existing lorekeep imports (after `from lorekeep.schema_io import load_schema`):

```python
from lorekeep.paths import resolve_paths
```

- [ ] **Step 2: Remove the `_paths()` function** — delete the entire `def _paths() -> dict[str, Path]:` block (currently lines 25-32, the function returning the dict with `LOREKEEP_*` env + CWD defaults).

- [ ] **Step 3: Rename all call sites** — replace every `p = _paths()` with `p = resolve_paths()`. There are 6 call sites: in `compile`, `eval_cmd`, `check`, `serve`, `mcp_add`, and `doctor`. Use a project-wide replace of the exact token `_paths()` → `resolve_paths()`.

- [ ] **Step 4: Run the full suite to verify nothing broke**

Run: `uv run pytest -q`
Expected: 100 passed (95 existing + 5 new from Task 2). All existing CLI tests set `LOREKEEP_*` env (tier 1) so they resolve identically; no command-body change.

- [ ] **Step 5: Manual dev-mode sanity check**

Run:
```bash
LOREKEEP_PROVIDER=fake uv run lorekeep compile
uv run lorekeep check
```
Expected: running from the repo (which has `.lorekeep/`) → dev mode → compiles into repo `graph/`, `check` prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/lorekeep/cli.py
git commit -m "refactor(cli): use resolve_paths() (dev/home/XDG); drop _paths()"
```

---

## Task 4: `defaults.py` — bootstrap constants

**Files:** Create `src/lorekeep/defaults.py`, Test `tests/test_defaults.py`

- [ ] **Step 1: Write the failing test `tests/test_defaults.py`**

```python
import json
import yaml
from lorekeep.defaults import DEFAULT_SCHEMA, DEFAULT_CONFIG_YAML
from lorekeep.config import Config


def test_default_schema_is_valid_json_v2():
    d = DEFAULT_SCHEMA
    assert d["version"] == 2
    assert "service" in d["node_types"]
    assert "concept" in d["node_types"]
    assert "relates_to" in d["edge_types"]
    json.dumps(d)  # serializable


def test_default_config_yaml_loads_into_config():
    cfg = yaml.safe_load(DEFAULT_CONFIG_YAML)
    c = Config.model_validate(cfg)
    assert c.provider.model.startswith("openai/")
    assert c.install_source == "pypi"
    assert c.ns.default == ["public"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_defaults.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lorekeep.defaults'`).

- [ ] **Step 3: Write `src/lorekeep/defaults.py`**

```python
"""Default config + schema used by `lorekeep init` to bootstrap a fresh home."""
from __future__ import annotations

DEFAULT_SCHEMA = {
    "version": 2,
    "node_types": {
        "service": {"props": {"name": "string", "lang": "string"}},
        "team": {"props": {"name": "string"}},
        "decision": {"props": {"title": "string"}},
        "project": {"props": {"name": "string", "status": "string"}},
        "person": {"props": {"name": "string", "role": "string"}},
        "tool": {"props": {"name": "string", "category": "string"}},
        "command": {"props": {"name": "string", "platform": "string"}},
        "concept": {"props": {"name": "string", "domain": "string"}},
        "note": {"props": {"title": "string", "topic": "string"}},
        "document": {"props": {"title": "string", "kind": "string"}},
    },
    "edge_types": {
        "depends_on": {"from": "service", "to": "service"},
        "decided_by": {"from": "decision", "to": "team"},
        "owns": {"from": "team", "to": "service"},
        "part_of": {"from": "service", "to": "project"},
        "uses": {"from": "service", "to": "tool"},
        "mentions": {"from": "note", "to": "concept"},
        "documents": {"from": "document", "to": "concept"},
        "describes": {"from": "note", "to": "service"},
        "relates_to": {"from": "concept", "to": "concept"},
    },
}

DEFAULT_CONFIG_YAML = """\
provider:
  backend: openai
  model: openai/gpt-4o-mini
  api_base: null
  api_key_env: OPENAI_API_KEY
  api_key: null
  temperature: 0.0
compile:
  chunk_lines: 60
ns:
  default: [public]
install_source: pypi
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_defaults.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/lorekeep/defaults.py tests/test_defaults.py
git commit -m "feat(defaults): DEFAULT_SCHEMA + DEFAULT_CONFIG_YAML for init"
```

---

## Task 5: `lorekeep init` command

**Files:** Modify `src/lorekeep/cli.py` (add `init`)

- [ ] **Step 1: Write the failing test `tests/test_init_cli.py`**

```python
from pathlib import Path
from typer.testing import CliRunner
from lorekeep.cli import app

runner = CliRunner()


def test_init_creates_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    assert (home / "config.yaml").exists()
    assert (home / "schema.json").exists()
    assert (home / "raw").is_dir()
    assert (home / "graph").is_dir()
    import json
    schema = json.loads((home / "schema.json").read_text())
    assert schema["version"] == 2


def test_init_preserves_existing_config(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    (home).mkdir()
    (home / "config.yaml").write_text("install_source: local\n")
    monkeypatch.setenv("LOREKEEP_HOME", str(home))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    # existing config not overwritten
    assert (home / "config.yaml").read_text() == "install_source: local\n"
    # but schema + dirs still created
    assert (home / "schema.json").exists()
    assert (home / "raw").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_init_cli.py -v`
Expected: FAIL (`Error: No such command 'init'`).

- [ ] **Step 3: Add the `init` command to `src/lorekeep/cli.py`** (before the `if __name__ == "__main__":` block). Also add the defaults import at the top:

Top of file, after `from lorekeep.paths import resolve_paths`:
```python
from lorekeep.defaults import DEFAULT_CONFIG_YAML, DEFAULT_SCHEMA
```

The command:
```python
@app.command()
def init() -> None:
    """Bootstrap the data home: config + schema + raw/graph dirs."""
    p = resolve_paths()
    created = []
    p["config"].parent.mkdir(parents=True, exist_ok=True)
    if not p["config"].exists():
        p["config"].write_text(DEFAULT_CONFIG_YAML)
        created.append(str(p["config"]))
    p["schema"].parent.mkdir(parents=True, exist_ok=True)
    if not p["schema"].exists():
        p["schema"].write_text(json.dumps(DEFAULT_SCHEMA, indent=2))
        created.append(str(p["schema"]))
    p["raw"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    typer.echo(f"home ready: config={p['config']}")
    typer.echo(f"  schema={p['schema']}  raw={p['raw']}  graph={p['out']}")
    if created:
        typer.echo(f"  wrote defaults: {created}")
    else:
        typer.echo("  (existing config/schema preserved)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_init_cli.py -v`
Expected: PASS (2 passed). Then `uv run pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/lorekeep/cli.py tests/test_init_cli.py
git commit -m "feat(cli): init command (bootstrap data home with defaults)"
```

---

## Task 6: Docs — data-home + dev/XDG/home flow + portable `.mcp.json`

**Files:** Modify `docs/serve.md`, `docs/compile.md`, `.lorekeep/config.yaml.example`

- [ ] **Step 1: Replace `docs/serve.md`** with this content:

````markdown
# Serving the knowledge graph to coding agents

Lorekeep resolves its data home with this precedence: explicit `LOREKEEP_*` env >
`LOREKEEP_HOME` > dev mode (`.lorekeep/` or `raw/` in CWD) > XDG default
(`~/.config/lorekeep` + `~/.local/share/lorekeep`).

## Installed use (recommended)

```bash
uvx lorekeep init                       # bootstrap ~/.config/lorekeep + ~/.local/share/lorekeep
# add your docs under ~/.local/share/lorekeep/raw/teams/<ns>/
LOREKEEP_PROVIDER=fake uvx lorekeep compile    # (or set a real provider in config)
uvx lorekeep mcp add --agent claude --ns teams/<ns>
uvx lorekeep doctor
```

`mcp add` writes a **portable** `.mcp.json` (no machine path) when
`install_source` is `pypi` (the default from `init`):

```json
{"mcpServers": {"lorekeep": {"command": "uvx",
  "args": ["lorekeep", "serve", "--transport", "stdio"],
  "env": {"LOREKEEP_NS": "teams/<ns>"}}}}
```

## Local dev (repo co-located data)

From the Lorekeep source checkout (has `.lorekeep/` → auto dev mode):

```bash
uv run lorekeep compile      # reads repo raw/, writes repo graph/
uv run lorekeep serve
```

Force dev mode anywhere: `LOREKEEP_DEV=1 lorekeep ...`.

## Custom knowledge base

```bash
LOREKEEP_HOME=~/kb-work uvx lorekeep init
LOREKEEP_HOME=~/kb-work uvx lorekeep compile
```

## Tools (read-only, scoped)

`search`, `get_node`, `neighbors`, `at_time`, `history`, `changes`,
`list_namespaces`, `schema`. Results are filtered to `LOREKEEP_NS`; cross-namespace
edges are hidden unless both endpoints are visible.
````

- [ ] **Step 2: Append to `docs/compile.md`** (after the existing content):

```markdown

## Data home

Lorekeep reads/writes data from a home resolved as: explicit `LOREKEEP_*` env >
`LOREKEEP_HOME` > dev mode (`.lorekeep/` or `raw/` in CWD) > XDG
(`~/.config/lorekeep` config, `~/.local/share/lorekeep` data). `lorekeep init`
bootstraps a fresh home. In a source checkout, dev mode is auto-detected so
`uv run lorekeep compile` uses the repo's `raw/` + `graph/`.
```

- [ ] **Step 3: Update `.lorekeep/config.yaml.example`** — change the `install_source` comment line to clarify portability. Replace the last line:

```yaml
install_source: pypi                              # pypi (portable .mcp.json) | local | git+URL | path
```

(The rest of the example stays; only this comment/line changes. If the file currently ends with `install_source: local`, change the value to `pypi` and update the comment as above.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/serve.md docs/compile.md .lorekeep/config.yaml.example
git commit -m "docs: data-home (dev/XDG/LOREKEEP_HOME) + init + portable .mcp.json"
```

---

## Task 7: End-to-end smoke (dev mode + home mode)

**Files:** none (verification only)

- [ ] **Step 1: Dev-mode smoke (repo)**

Run:
```bash
LOREKEEP_PROVIDER=fake uv run lorekeep compile
uv run lorekeep check
```
Expected: compiles into repo `graph/` (dev mode auto-detected via `.lorekeep/`); `check` prints `ok`.

- [ ] **Step 2: Home-mode smoke (isolated)**

Run:
```bash
LOREKEEP_HOME=/tmp/lorekeep-smoke LOREKEEP_PROVIDER=fake uv run lorekeep init
# seed a doc
mkdir -p /tmp/lorekeep-smoke/raw/teams/backend
cp raw/teams/backend/payments.md /tmp/lorekeep-smoke/raw/teams/backend/
LOREKEEP_HOME=/tmp/lorekeep-smoke LOREKEEP_PROVIDER=fake uv run lorekeep compile
LOREKEEP_HOME=/tmp/lorekeep-smoke LOREKEEP_NS=teams/backend uv run lorekeep doctor
```
Expected: `init` creates `/tmp/lorekeep-smoke/{config.yaml,schema.json,raw,graph}`; `compile` writes `/tmp/lorekeep-smoke/graph/facts.jsonl`; `doctor` prints `all checks passed: 4 nodes, 2 edges`.

- [ ] **Step 3: Portable `.mcp.json` smoke**

Run:
```bash
mkdir -p /tmp/lorekeep-mcp-smoke && cd /tmp/lorekeep-mcp-smoke
LOREKEEP_HOME=/tmp/lorekeep-smoke LOREKEEP_CONFIG=/tmp/lorekeep-smoke/config.yaml uvx --from /home/manhpt1/Workspace/lorekeep lorekeep mcp add --agent claude --ns teams/backend
cat .mcp.json
```
Expected: `.mcp.json` command is `uvx` with args `["lorekeep","serve","--transport","stdio"]` (NO `--from` / machine path, because `init` set `install_source: pypi`).

- [ ] **Step 4: Clean up smoke artifacts**

Run: `rm -rf /tmp/lorekeep-smoke /tmp/lorekeep-mcp-smoke`

- [ ] **Step 5: Confirm full suite green**

Run: `uv run pytest -q`
Expected: all PASS (≈104 tests).

(No commit — verification only.)

---

## Self-Review (run after writing)

**Spec coverage:**
- 4-tier path resolution (env/home/dev/XDG) → Task 2 ✓
- `lorekeep init` bootstrap → Task 5 ✓
- dev mode preserves current layout (zero migration) → Task 2 (dev branch) ✓
- portable `.mcp.json` (no machine path) when install_source=pypi → Task 6 + Task 7 smoke ✓ (resolve_command from Plan B already handles pypi→`uvx lorekeep serve`)
- platformdirs dep → Task 1 ✓
- tests unchanged (tier-1 env) → Task 3 Step 4 confirms ✓
- docs → Task 6 ✓

**Placeholder scan:** No TBD/TODO/"add error handling". Every code step has complete code. `init` is fully specified.

**Type consistency:** `resolve_paths()` returns `dict[str, Path]` with keys `raw/out/cache/schema/config` — identical to the old `_paths()`, so all 6 command call sites work unchanged except the rename. `DEFAULT_SCHEMA` matches the v2 schema committed in `graph/schema.json` (Plan A follow-up). `DEFAULT_CONFIG_YAML` fields match `ProviderConfig`/`CompileConfig`/`NsConfig`/`Config` (incl. `api_key_env`, `api_key`, `install_source`).
