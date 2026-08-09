# Importing coding-agent knowledge

Agent knowledge reaches `raw/` through two different mechanisms:

1. automatic hooks/watcher capture memory files and bounded transcript Markdown
   without an extra LLM call; and
2. `lorekeep import` is a manual path whose default deep mode uses the configured
   provider to summarize a selected session.

Both produce ordinary Markdown. `lorekeep compile` later extracts graph facts
from it. Separately, an agent can write a structured proposal directly to a
namespace journal through MCP; see [Serving over MCP](serve.md#journal-based-writes).

## Supported sources

| Source | Memory source | Session source | Manual modes |
|---|---|---|---|
| `claude` | `memory/*.md` | project transcript JSONL | `--quick` memory copy; default deep summary |
| `codex` | `$CODEX_HOME/memories/*.md` | rollout JSONL under `$CODEX_HOME/sessions/` | `--quick` memory copy; default deep summary |
| `cursor` | none | global Cursor `state.vscdb` composer data | deep only |
| `opencode` | none | opencode SQLite session database | deep only |

The registry is the source of truth for detection paths, config/hook writers,
and import functions. `lorekeep agent detect` shows whether each agent is
installed, has local session data, and is wired.

## Automatic zero-LLM capture

`init`, `mcp add`, and `agent wire` install supported session-end hooks. The
hidden hook command is an internal integration detail; it:

- copies changed Claude/Codex memory files;
- renders supported current sessions from all four agents into bounded,
  deterministic Markdown batches;
- caps batch count/size and retains only the configured recent sessions; and
- uses SHA-256 manifests so unchanged content is not rewritten.

`agent watch` also re-discovers memory/session sources while running. Memory or
transcript output appears in these namespaces:

```text
raw/claude-memory/     raw/claude-session/
raw/codex-memory/      raw/codex-session/
raw/cursor-session/    raw/opencode-session/
```

The hook writes raw Markdown only. With the watcher running, the raw mtime change
triggers compile on a later cycle. Without it, run `lorekeep compile` yourself.

Control automatic transcript capture in config:

```yaml
agents:
  enabled: [claude, codex, cursor, opencode]
  watch_transcripts: true
  transcript_max_batches: 20
  transcript_max_chars: 20000
  transcript_retain_sessions: 5
```

## Manual Claude import

```bash
lorekeep import --from claude --quick
lorekeep import --from claude
lorekeep import --from claude --session-path ~/.claude/projects/<project>
```

- `--quick` copies changed memory Markdown and does not instantiate a provider.
- Default deep mode also parses and summarizes the selected transcript with the
  configured provider.
- Override destinations with `--memory-ns` and `--session-ns`.

## Manual Codex import

```bash
lorekeep import --from codex --quick --memory-ns codex-memory
lorekeep import --from codex --session-ns codex-session
lorekeep import --from codex --session-path /path/to/rollout.jsonl
```

Codex supports the same quick/deep split. Pass `--memory-ns codex-memory` on the
manual quick command: the current generic CLI option retains the historical
`claude-memory` default, while automatic registry-driven capture already uses
the correct `codex-memory` namespace.

## Manual Cursor import

Cursor's manual importer reads composer records from the global
`globalStorage/state.vscdb` and summarizes locally available conversations:

```bash
lorekeep import --from cursor
CURSOR_STATE_DB=/path/to/state.vscdb lorekeep import --from cursor
lorekeep import --from cursor --session-path /path/to/globalStorage
```

`--quick` is rejected because Cursor has no separate curated memory directory.
Some Cursor installations persist only conversation headers locally and fetch
the body from cloud storage. Header-only records are skipped, so zero output can
mean there is no local transcript rather than a broken database.

## Manual opencode import

```bash
lorekeep import --from opencode
lorekeep import --from opencode --session-path <session-id>
```

The importer locates the current project session in opencode's SQLite database,
then uses the configured provider to summarize it. `--quick` is rejected; the
automatic hook/watcher transcript renderer is the zero-LLM alternative.

## Preview and idempotency

Use `--dry-run` to inspect destination counts/paths without writing:

```bash
lorekeep import --from codex --quick --memory-ns codex-memory --dry-run
```

Import manifests under each generated raw namespace store content hashes.
Unchanged items are skipped, and deterministic transcript batches preserve an
unchanged prefix as a session grows. This keeps raw mtimes stable and prevents
unnecessary compile/cache misses.

## Compile imported Markdown

```bash
lorekeep compile
```

Imported material has the same compile-time schema validation and provenance as
handwritten raw docs. Review generated raw Markdown when source conversations may
contain sensitive or low-quality content; it is durable and can be included in
the private backup remote.

## Related

- [Getting started](getting-started.md)
- [Compiling and resolving](compile.md)
- [Autonomous agent architecture](../architecture/agent.md)
- [Data home and paths](data-home.md)
