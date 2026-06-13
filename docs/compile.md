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
