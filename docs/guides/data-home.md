# Data home and path resolution

Every CLI, daemon, and MCP process calls the same pure `resolve_paths()` logic.
Precedence is high to low:

1. explicit per-path environment override;
2. `LOREKEEP_HOME`;
3. development mode; and
4. the default dotdir `~/.lorekeep/`.

## Path map

| Logical path | Environment override | Home-relative default |
|---|---|---|
| raw Markdown | `LOREKEEP_RAW` | `raw/` |
| graph output | `LOREKEEP_OUT` | `graph/` |
| extraction cache | `LOREKEEP_CACHE` | `cache.json` |
| schema | `LOREKEEP_SCHEMA` | `schema.json` |
| config | `LOREKEEP_CONFIG` | `config.yaml` |
| journals | `LOREKEEP_PENDING` | `pending/` |
| generated wiki | `LOREKEEP_WIKI` | `wiki/` |
| runtime logs | `LOREKEEP_LOGS` | `logs/` |
| lifecycle event queue | — | `hook-events/` |

Per-path overrides take precedence independently; setting `LOREKEEP_RAW` does
not relocate the other paths.

## Default dotdir mode

With no override/home/dev marker, Lorekeep uses `~/.lorekeep/`:

- all data — config, raw, graph, schema, pending, wiki, cache, and logs — lives
  under one dotdir in the user home directory.

This works identically on Linux, macOS, and Windows:

```text
~/.lorekeep/
├── config.yaml
├── schema.json
├── raw/
├── graph/
├── pending/
├── hook-events/
├── wiki/
├── logs/
└── cache.json
```

Bootstrap and inspect the paths printed by the command:

```bash
lorekeep init
lorekeep agent profile
```

Do not assume Linux paths on macOS or Windows; use the output or set an explicit
home.

## Explicit home

`LOREKEEP_HOME` makes config and data use one directory:

```bash
LOREKEEP_HOME=~/knowledge/work lorekeep init --no-watch
LOREKEEP_HOME=~/knowledge/work lorekeep compile
LOREKEEP_HOME=~/knowledge/work lorekeep serve
```

This is useful for a separate knowledge base, portable private backup clone, or
isolated smoke test. Agent MCP configuration must include the same environment
when it should serve that home.

## Development mode

When the current directory contains `.lorekeep/`, or `LOREKEEP_DEV=1`, all
home-relative paths resolve under `<cwd>/.lorekeep/`:

```bash
uv run lorekeep compile
uv run lorekeep doctor
uv run lorekeep serve
```

The Lorekeep source checkout includes this marker, so development commands use
the repository-local data home without migrating installed data.

## Per-path overrides

Power users and tests can pin only the required paths:

```bash
LOREKEEP_RAW=./fixtures/raw \
LOREKEEP_OUT=/tmp/lorekeep-graph \
LOREKEEP_CACHE=/tmp/lorekeep-cache.json \
uv run lorekeep compile
```

Relative override values are interpreted relative to the process working
directory after `Path.expanduser`; they are not automatically made relative to
`LOREKEEP_HOME`.

## Initialization side effects

`resolve_paths()` itself performs no I/O. `init` creates config/schema/raw/graph/
pending as needed and preserves existing config/schema. Wiki, logs, and the
device-local `hook-events/` queue are created lazily by compile/wiki, runtime
logging, and lifecycle hooks. The queue is excluded from backup because its
transcript paths belong to one device. In first-run setup, profile files, agent
configuration/hooks, imported memory files, compiled artifacts, and a
background watcher may also be created as described in
[Getting started](getting-started.md).

## Related

- [Getting started](getting-started.md)
- [Compile and resolve](compile.md)
- [Serving over MCP](serve.md)
- [Backing up and syncing](backup.md)
