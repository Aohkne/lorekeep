# Compiling the knowledge graph

## 1. Add raw docs

Drop markdown under `raw/<namespace>/`:

```
raw/backend/payments.md
raw/frontend/web.md
```

The first directory under `raw/` becomes the fact's `ns` (e.g. `backend`, `frontend`).

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

## Next: serve to agents

See [serve.md](serve.md) to expose the graph to Claude Code / Cursor / Codex over MCP.

## Data home

Laputa reads/writes data from a home resolved as: explicit `LAPUTA_*` env >
`LAPUTA_HOME` > dev mode (`.laputa/` or `raw/` in CWD) > XDG
(`~/.config/laputa` config, `~/.local/share/laputa` data). `laputa init`
bootstraps a fresh home. In a source checkout, dev mode is auto-detected so
`uv run laputa compile` uses the repo's `raw/` + `graph/`.
