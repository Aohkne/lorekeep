# Backing up and syncing the data home

`lorekeep backup` creates a separate Git repository **inside the resolved data
home** and pushes it to a private remote. This is independent of the Lorekeep
source-code repository.

Use backup for durable knowledge inputs, not generated graph output.

## What is durable

The important tracked inputs are:

- `raw/` source Markdown, including deterministic session captures and import
  manifests;
- `schema.json`; and
- `pending/` journals, including accepted, pending, flagged, or quarantined
  entries needed for replay and audit on another device.

The generated backup `.gitignore` excludes:

```text
config.yaml
graph/facts.jsonl
graph/manifest.json
wiki/
cache.json
fts.sqlite
*.lock
```

Git stages all other unignored files under the data home with `git add -A`; it is
not an allowlist implementation. Keep the data home dedicated to Lorekeep and
inspect what will be committed. Runtime logs are privacy-redacted but are not
currently in the generated ignore list. Add `logs/` to that backup repository's
`.gitignore` (and untrack already committed logs) when you do not want
operational metadata synced.

`config.yaml` is deliberately ignored because it may contain an inline API key.
In XDG mode it lives outside the data home anyway. Each device configures its own
provider credentials.

Journals can contain sensitive facts and review notes, including quarantined
content. The remote must be private even though derived artifacts and config are
excluded.

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

The command stages current unignored changes, creates a timestamped commit when
needed, and always attempts a push. That final push means a previous network or
non-fast-forward failure can be retried even when no new local file changed.

Manual backup does **not** automatically fetch/rebase before every push. If the
remote moved, it reports the Git failure so you can reconcile explicitly.

## Watcher synchronization

When `agent watch` finds a configured remote:

- startup attempts fetch + rebase before journal replay, so remote journal
  events are visible locally first;
- after a successful compile it commits local durable changes, fetches/rebases,
  and pushes; and
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
lorekeep init --yes --no-watch
# configure this device's provider key/model
lorekeep compile
lorekeep doctor
```

For the normal installed Linux layout, clone into the platform data directory
(`~/.local/share/lorekeep`) and let config remain at
`~/.config/lorekeep/config.yaml`. See [Data home and paths](data-home.md) for
macOS/Windows/custom locations.

`init` preserves the cloned schema/raw/journals, creates missing local config and
directories, and rewires agents. Compile rebuilds the ignored graph, manifest,
wiki, and cache. Accepted journal entries are replayed so agent-contributed facts
survive the rebuild.

## Resolve a multi-device conflict

Git sync is sequential rather than conflict-free. If another device pushed first:

```bash
cd <resolved-data-home>
git fetch origin
git rebase origin/$(git branch --show-current)
# resolve ordinary text conflicts if Git stops
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

Conflicts in generated `facts.jsonl`, manifest, cache, or wiki should not occur
because those paths are ignored and rebuilt locally.

## Current multi-device guarantee

Lorekeep currently provides:

- private Git transport;
- deterministic rebuild of `facts.jsonl` from durable inputs;
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
