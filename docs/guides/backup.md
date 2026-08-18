# Backing up and syncing the data home

`lorekeep backup` creates a separate Git repository **inside the resolved data
home** and pushes it to a private remote. This is independent of the Lorekeep
source-code repository.

Backup contains both durable knowledge inputs and the latest published
graph/wiki snapshot. A restored device can query the graph and open the wiki
without paying for an immediate LLM recompile.

## What is durable

The tracked durable inputs are:

- `raw/` source Markdown, including deterministic session captures and import
  manifests;
- `schema.json`; and
- `pending/` journals, including accepted, pending, flagged, or quarantined
  entries needed for replay and audit on another device.

The tracked read-through snapshot is:

- `graph/facts.jsonl` and `graph/manifest.json`; and
- generated Markdown under `wiki/`, including its append-only `log.md`.

Durable inputs remain the source of truth. Graph/wiki files are a convenience
snapshot, not an independently editable copy of the knowledge base.

The generated backup `.gitignore` excludes:

```text
config.yaml
cache.json
fts.sqlite
graph/fts.sqlite
graph/*.tmp
.wiki-build.tmp/
.wiki-rollback.tmp/
wiki/.obsidian/
wiki/.trash/
wiki/.DS_Store
logs/
hook-events/
.daemon.pid
*.lock
```

Git stages all other unignored files under the data home with `git add -A`; it is
not an allowlist implementation. Keep the data home dedicated to Lorekeep and
inspect what will be committed. Existing backup repositories are migrated on
the next backup or watcher sync: obsolete graph/wiki ignore rules are removed,
custom ignore rules are preserved, and already tracked device-local files are
removed from the index without deleting local copies.

`config.yaml` is deliberately ignored because it may contain an inline API key.
Each device configures its own provider credentials. `hook-events/` is also
local-only: queued records contain transcript paths that are meaningful only on
the device where the coding-agent lifecycle event fired. The resulting
`raw/<agent>-session/` Markdown is durable and is backed up normally.

Journals can contain sensitive facts and review notes, including quarantined
content. The graph and wiki project the full local graph rather than a caller's
namespace-scoped view. The remote must therefore be private and protected for
the most sensitive namespace it contains.

Lorekeep also writes a managed `.gitattributes` block. `facts.jsonl`, the
manifest, and generated wiki pages are marked non-mergeable. This prevents Git
from silently combining lines from two independent compiles into a graph/wiki
snapshot that Lorekeep never produced. `wiki/log.md` retains normal text merge
behavior because it is append-only human-readable history.

## Initialize a private remote

Create an empty private repository, then:

```bash
lorekeep backup --init https://github.com/<you>/lorekeep-data.git
```

The command:

1. initializes Git in the data home when needed;
2. writes the ignore rules above;
3. creates or updates `origin`;
4. commits tracked content with an internal backup identity; and
5. pushes the current branch and sets its upstream.

It is safe to rerun against the same data home. When a Git repository already
exists, initialization attempts fetch + rebase before push.

For a local private remote:

```bash
git init --bare /secure/path/lorekeep-data.git
lorekeep backup --init /secure/path/lorekeep-data.git
```

## Manual backup

```bash
lorekeep backup
```

The command migrates backup metadata, stages current unignored changes, creates
a timestamped commit when needed, fetches/rebases the current remote branch, and
always attempts a push. A previous network or non-fast-forward failure can be
retried even when no new local file changed.

### Auto-resolve snapshot conflicts

```bash
lorekeep backup --force
```

When two devices publish different graph/wiki snapshots, the rebase conflicts on
generated files (`facts.jsonl`, `manifest.json`, wiki pages). `--force` resolves
these automatically by accepting the remote version — the local device will
publish a fresh, complete snapshot on its next compile. This is safe because
generated files are deterministic from durable inputs; they are snapshots, not
independent edits.

Durable input conflicts (`raw/`, `schema.json`, `pending/`) abort by default.
When `backup.auto_resolve_durable` is enabled, JSON is merged structurally,
JSONL journal entries are deduplicated by entry id, and Markdown is merged with
the configured provider. Any conflict that cannot be resolved safely still
aborts for manual reconciliation.

## Automatic backup (daemon)

The daemon (running via `agent watch` or as a persistent service) backs up
automatically when `agents.auto_backup` is `true` (default). Backup runs at five
trigger points:

1. **Startup** — syncs with remote before entering the poll loop.
2. **After compile** — commits new graph/wiki snapshot plus any raw/ changes.
3. **After self-heal** — if self-heal modified facts (dangling edge removal,
   dedup), commits and pushes the updated graph.
4. **After resolve** — when pending journals were merged, commits and pushes.
5. **After external compile** — when another process (CLI, serve, another
   daemon) changed `manifest.json`, the daemon detects the mtime change and
   backs up so externally-compiled graph changes are not lost.

The daemon always calls backup with `auto_fix=True` (equivalent to `--force`),
so snapshot conflicts are resolved silently. All backup calls are best-effort:
network failures, missing remotes, and rebase errors are logged but never crash
the daemon.

## Watcher synchronization

When `agent watch` finds a configured remote:

- startup attempts fetch + rebase before journal replay, so remote journal
  events and the latest snapshot are visible locally first;
- after a successful compile it commits local durable changes plus graph/wiki
  snapshot, fetches/rebases, and pushes; and
- network/rebase conflicts are logged and skipped so the watcher stays alive.

No remote means these steps are silent no-ops. Use `lorekeep support` or inspect
the runtime log when automatic synchronization appears stale.

## Restore on a new device

Clone only into a path that does not already contain data. Move any existing
data home aside first; do not delete it until the restored graph is verified.

Custom explicit home example:

```bash
git clone https://github.com/<you>/lorekeep-data.git ~/lorekeep-data
export LOREKEEP_HOME=~/lorekeep-data
# Avoid an eager compile while init creates the local-only config.
env -u OPENAI_API_KEY lorekeep init --yes --no-watch
# Configure this device's provider key/model when future inputs need compile.
lorekeep doctor
```

For the normal installed layout, clone into the data directory
(`~/.lorekeep`). See [Data home and paths](data-home.md) for custom locations.

`init` preserves the cloned schema/raw/journals/graph/wiki, creates missing local
config and directories, and rewires agents. The cloned MCP graph and wiki are
immediately usable; FTS is rebuilt lazily. Run `compile` only after inputs have
changed, when the restored manifest is stale, or when you intentionally want a
new provider/schema extraction. Accepted journal entries are replayed on that
next compile so agent-contributed facts survive publication.

## Resolve a multi-device conflict

Git sync is sequential rather than conflict-free. `lorekeep backup` handles a
clean/disjoint remote advance with fetch + rebase automatically. If both devices
changed the same durable input or both published different snapshots, Lorekeep
aborts the failed rebase and restores the local repository instead of leaving it
stuck. Reconcile explicitly:

```bash
cd <resolved-data-home>
git fetch origin
git rebase origin/$(git branch --show-current)
# Resolve raw/, schema.json, and pending/ as ordinary source conflicts.
# If graph/ or wiki/ conflicts, do not combine their lines; rebuild the one
# complete snapshot from the now-combined durable inputs.
lorekeep compile
git add raw schema.json pending graph wiki
git rebase --continue
cd -
lorekeep backup
```

Before resolving a journal conflict, preserve every valid JSONL line and avoid
duplicating entry ids. Then run:

```bash
lorekeep resolve
lorekeep doctor
```

`cache.json`, FTS, lifecycle event queues, runtime logs, and Obsidian device
settings remain local and cannot conflict. A concurrent graph/wiki conflict necessarily requires one
compile after durable inputs converge: neither device's old snapshot can fully
represent the union of both devices' new inputs. Outside that concurrency case,
the backed-up snapshot avoids recompilation on restore.

## Current multi-device guarantee

Lorekeep currently provides:

- private Git transport;
- immediately restorable graph/wiki snapshots;
- deterministic rebuild of `facts.jsonl` from durable inputs;
- non-mergeable generated snapshots with clean rebase abort on conflict;
- process-safe local journal append/status rewrite;
- collision-resistant journal entry ids and deterministic merge ordering; and
- accepted-journal replay after remote synchronization.

It does not yet provide simultaneous co-editing, a central conflict resolver, or
an authenticated always-on sync server. Concurrent changes to the same raw file
or namespace journal can require manual Git reconciliation.

## Related

- [Data home and paths](data-home.md)
- [Compile and resolve](compile.md)
- [Journal architecture](../architecture/journal.md)
- [Runtime logging](runtime-logging.md)
