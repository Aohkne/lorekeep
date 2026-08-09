# Autonomous agent and graph operations

Lorekeep's “agent” surface combines an event-driven watcher with explicit
one-shot graph/source operations. It is not a planner or periodic job scheduler.

## Public commands

| Command | Current role | LLM use |
|---|---|---|
| `agent detect` | report installed, active, session-data, wiring/hook state | none |
| `agent wire` | idempotently write MCP config + supported hooks | none |
| `agent watch` | poll sources/journals/agents and coordinate maintenance | compile only when triggered |
| `agent service install/uninstall/status` | OS persistence wrapper around `agent watch` | none |
| `agent ingest` | extract/review one raw file and journal approved facts | yes |
| `agent lint` | structural/semantic graph heuristics | none |
| `agent lint --auto-fix` | remove dangling/duplicate edges and republish | none |
| `agent suggest` | list missing dates/sources, sparse namespaces/edges | none |
| `agent status` | graph counts, namespaces, lint/pending counts | none |
| `agent profile` | print/open editable personal raw source | none |
| `agent contribution` | find personal-only shareable entities | none |

There is no `agent evolve` command.

## Detection and wiring

The registry defines six clients: Claude Code, Codex, Cursor, opencode, Grok Build, and Qoder.
Each `AgentSpec` owns detection markers, active-shell env vars, config/hook
targets, and memory/session importer functions.

`detect_active_agent` examines known environment markers. Installed detection
checks client directories and binaries. The active client is listed first but
does not exclude other installed clients; Lorekeep is designed to aggregate all
of them.

`agent wire` uses registry writers and preserves unrelated native config. It can:

- wire all detected/enabled agents;
- target one explicit `--agent`;
- use project or user `--scope`;
- set one MCP namespace;
- preview with `--dry-run`; and
- include undetected agents with `--force`.

Writers report unchanged without rewriting. The watcher can therefore re-run
detection periodically without causing config mtime churn. Failed clients enter
a one-hour per-process backoff.

## Watcher startup

`agent watch`:

1. prints resolved raw/pending paths and mode;
2. refuses to start when `.daemon.pid` belongs to a live process;
3. writes its PID and installs SIGTERM cleanup;
4. snapshots the installed package version;
5. loads `agents` config;
6. synchronizes the private backup remote when configured; and
7. replays pending/accepted journals after sync before entering the loop.

Startup synchronization precedes replay so remote journal events are available
on this device.

## Poll loop

The loop sleeps `--interval` seconds (default 60) and performs these checks in
order.

### Package upgrade

The running process compares its startup distribution version to current
on-disk metadata. When an external `uv tool upgrade`/install changes it, the
watcher removes the PID file and replaces itself with `os.execv`, picking up new
code without waiting for systemd/launchd restart.

### Agent wiring

On first pass and every `agents.wire_interval_seconds` (default 900), it detects
installed clients and idempotently wires enabled ones according to
`agents.wire_scope`.

### Raw/schema compile

The watcher tracks both Markdown file count and maximum mtime, plus schema mtime.
After the initial baseline, any count change, newer raw mtime, or newer schema
mtime runs the same compile pipeline as the CLI. File count catches new files
even on filesystems where mtimes share a coarse tick.

Compile errors are reported/logged but do not kill the daemon. Total provider
failure does not terminate the loop.

After successful compile it:

1. synchronizes backup if configured;
2. replays/merges journals;
3. runs deterministic self-heal when `agents.self_heal` is true; and
4. generates the wiki once from final facts.

### Journal resolve

The watcher tracks maximum mtime across `pending/**/journal.jsonl`. A later mtime
triggers immediate auto-resolve in that poll cycle. There is no batch-size or
five-minute threshold.

### Memory quick import

Every cycle re-discovers registry agents with a curated memory source (currently
Claude and Codex). First sight or newer memory mtime triggers content-hash-based
quick import, rate-limited to once per 30 seconds per source. State advances only
after success, so a failed import can retry.

### Transcript dump

When `agents.watch_transcripts` is true, the registry locates supported live
sessions for all supported clients every 30 seconds. Parsed turns become bounded,
deterministic Markdown batches under `raw/<agent>-session/`; older sessions are
pruned according to retention config. No LLM is called here.

Because raw detection occurs earlier in the same loop, newly dumped transcript
Markdown is compiled on a later poll.

## Self-heal

`agent.self_heal` is deliberately narrow:

- remove edges whose real endpoint node is missing;
- deduplicate edges with identical type/endpoints/validity;
- report circular dependencies; and
- report orphan nodes without deleting them.

The pure function returns a new `GraphStore` plus `HealReport`; callers decide
whether to publish. The daemon runs it only after a successful compile. The CLI
also previews fixability during `agent lint` and persists it with
`agent lint --auto-fix`.

Self-heal does not synthesize facts, resolve semantic contradictions, fill
descriptions, or alter raw sources.

## Lint, suggest, and status

`lint` currently detects:

- orphan nodes;
- missing edge endpoints;
- expired edges;
- very sparse namespaces relative to the graph; and
- duplicate node ids with conflicting props when such duplicates reach the
  loaded store.

`suggest` lists nodes with one/fewer sources, missing `valid_from` on such nodes,
single-namespace graphs, and graphs with no edges. It is deterministic graph
analysis despite the command name; no provider call occurs.

`status` reports global (unscoped local) node/edge/namespace counts, total lint
issues, and pending-journal count. For namespace-filtered agent-facing status,
use the MCP `context(section="status")` tool.

None of these one-shot commands runs nightly/weekly from the watcher.

## Conversational ingest

`agent ingest <raw-file>` requires the file to resolve beneath `raw/`. It chunks
and extracts using the configured provider, displays candidates, and lets the
user approve all or individual facts. Approved facts are appended with
`agent="cli-ingest"` and confidence 1.0. Resolve/compile is still required before
they are served.

## Persistent service

`agent service install` generates a platform wrapper:

- Linux: systemd user unit;
- macOS: launchd LaunchAgent; and
- Windows: Startup-folder VBS.

Each wrapper runs `lorekeep agent watch` and pins `LOREKEEP_HOME` to the home
resolved at install time. Status delegates to the platform service manager.
Reinstall when the desired home/command installation changes.

## Error resilience

Each loop body has a broad error boundary: unexpected errors are logged, shown,
and followed by the normal interval sleep. Individual wiring, import, backup,
compile, transcript, resolve, and wiki operations also isolate expected failure
so one integration does not stop all maintenance.

## Not implemented

The following belong in the roadmap, not the current daemon description:

- nightly semantic reconcile/lint;
- weekly suggestions/digests;
- hourly batch import schedule;
- autonomous schema evolution;
- provider-backed gap filling without user review; and
- a distributed action queue across devices.

## Related

- [Pipeline](pipeline.md)
- [Journal](journal.md)
- [Import guide](../guides/import.md)
- [Runtime logging](../guides/runtime-logging.md)
- [Roadmap](../ROADMAP.md)
