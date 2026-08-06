# Runtime logging and bug reports

Lorekeep writes operational logs for every CLI, daemon, and MCP process to:

```text
<LOREKEEP_HOME>/logs/lorekeep.log
```

The path follows normal data-home resolution. Set `LOREKEEP_LOGS` to override
only the log directory. In a source checkout it is normally
`.lorekeep/logs/lorekeep.log`; an installed Linux build normally uses
`~/.local/share/lorekeep/logs/lorekeep.log`.

The log is plain text, timestamps are UTC, and each entry includes a component,
event, process ID, and per-process run ID. It rotates at 5 MiB and keeps five
backups. `--verbose` or `LOREKEEP_DEBUG=1` enables DEBUG entries; `--quiet`
records warnings and errors only for that invocation.

Logs deliberately exclude prompts, MCP queries/results, raw documents, fact
properties, and journal content. Credential-shaped values and home paths in
third-party errors are redacted. Never paste `config.yaml`, `facts.jsonl`, raw
documents, or pending journals into a public issue.

## Prepare a GitHub issue

One command prints a copy/paste-ready metadata report and creates the ZIP to
attach to the same issue:

```bash
lorekeep support
# ...Markdown report...
# support bundle: ./lorekeep-support-20260804T120000Z.zip
# sha256: ...
```

Choose the destination, or request only one side of the workflow:

```bash
lorekeep support --output ./support.zip
lorekeep support --report-only
lorekeep support --no-print --output ./support.zip
```

The ZIP has a strict allowlist: `report.md`, the last 1,000 runtime log lines
after a second redaction pass, and a manifest counter summary. It never
contains raw data, facts, configuration files, caches, or journals. The old
`support report` and `support bundle` forms remain hidden compatibility aliases.

## Daemon startup logs

The unified runtime log is authoritative. Platform launchers can also retain
small bootstrap logs for failures before Lorekeep starts:

- systemd: `journalctl --user-unit lorekeep --since today`
- launchd: `<LOREKEEP_HOME>/logs/daemon-bootstrap.log` and `.err.log`
- `lorekeep init` background process: `<LOREKEEP_HOME>/logs/daemon-bootstrap.log`

Older installations may still have `agent.log`, `daemon.log`, or
`daemon.err.log` in the data-home root. They are not deleted automatically and
are reported only by filename/status in a support report; their contents are
never added to a support bundle.

## Quick diagnosis

```bash
lorekeep doctor
lorekeep agent service status
lorekeep support
```

For MCP startup only, a timeout is expected because stdio waits for a client:

```bash
timeout 3 lorekeep serve --transport stdio </dev/null
```
