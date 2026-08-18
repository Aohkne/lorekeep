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
| `claude` | `~/.claude/projects/*/memory/*.md` | project transcript JSONL beside memory | `--quick` memory copy; default deep summary |
| `codex` | `~/.codex/memories/*.md` | rollout JSONL under `~/.codex/sessions/` | `--quick` memory copy; default deep summary |
| `cursor` | none | global Cursor `state.vscdb` composer data | deep only |
| `opencode` | none | opencode SQLite session database | deep only |
| `grok` | none | `~/.grok/sessions/` JSONL | zero-LLM transcript dump |
| `qoder` | none | `~/.qoder/projects/` JSONL | zero-LLM transcript dump |
| `copilot` | none | `~/.copilot/session-state/<id>/events.jsonl` | zero-LLM transcript dump |
| `cmd` | none | `~/.commandcode/projects/` JSONL | zero-LLM transcript dump |

The displayed homes are defaults. `CLAUDE_CONFIG_DIR`, `CODEX_HOME`,
`GROK_HOME`, `QODER_CONFIG_DIR`, and `COPILOT_HOME` relocate their respective
agent roots.

The registry is the source of truth for detection paths, config/hook writers,
and import functions. `lorekeep agent detect` shows whether each agent is
installed, has local session data, and is wired.

## Automatic zero-LLM capture

`init`, `mcp add`, and `agent wire` install each client's best available
lifecycle event. Claude, Codex, Cursor, Grok, Qoder, and Copilot CLI have exact
session-end events. opencode's `session.idle` and Command Code's end-of-turn
`Stop` are fallbacks, so Lorekeep waits for a quiet period before capture. The
full event/scope matrix is canonical in
[Autonomous agent: lifecycle contracts](../architecture/agent.md#lifecycle-capture-contracts).

The hidden hook command only writes a bounded event record and exits. The daemon
later reads the one named transcript, renders deterministic Markdown, and
compiles it in the same poll cycle. Failed imports remain queued with backoff;
`lorekeep doctor` shows their state. Claude/Codex curated memory files remain a
separate cheap polling path. Output namespaces are:

```text
raw/claude-memory/     raw/claude-session/
raw/codex-memory/      raw/codex-session/
raw/cursor-session/    raw/opencode-session/
raw/grok-session/      raw/qoder-session/
raw/copilot-session/   raw/cmd-session/
```

The event queue under `hook-events/` is private device-local state and is not
backed up; generated raw Markdown is durable and follows normal backup rules.
Without the watcher, events remain queued until it starts.

Control automatic transcript capture in config:

```yaml
agents:
  enabled: [claude, codex, cursor, opencode, grok, qoder, copilot, cmd]
  watch_transcripts: true
  transcript_max_batches: 20
  transcript_max_chars: 20000
  transcript_retain_sessions: 5
  session_end_idle_seconds: 300
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

## Manual zero-LLM transcript import

Grok, Qoder, Copilot CLI, and Command Code reuse their automatic transcript
adapter without an extra provider call:

```bash
lorekeep import --from grok
lorekeep import --from qoder --session-path ~/.qoder/projects/<project>/<id>.jsonl
lorekeep import --from copilot --session-path ~/.copilot/session-state/<id>/events.jsonl
lorekeep import --from cmd --session-path ~/.commandcode/projects/<project>/<id>.jsonl
```

Use `--session-ns` to override the normal `<agent>-session` destination and
`--dry-run` to preview file counts. `--quick` is not applicable because these
adapters already avoid the LLM.

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
