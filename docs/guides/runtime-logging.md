# Runtime logging and bug reports

Every CLI, daemon, and MCP process writes a unified operational log to:

```text
<resolved-logs-dir>/lorekeep.log
```

The directory follows normal [path resolution](data-home.md) and can be
overridden with `LOREKEEP_LOGS`. Typical locations are
`.lorekeep/logs/lorekeep.log` in development mode and
`~/.lorekeep/logs/lorekeep.log` when installed.

## Log contract

Runtime logs are plain text with UTC timestamps, component, event, PID, and a
per-process run id. Rotation starts at 5 MiB and keeps five backups.

```bash
lorekeep --verbose doctor       # DEBUG for this process
LOREKEEP_DEBUG=1 lorekeep doctor
lorekeep --quiet compile        # warnings/errors only
```

Logging deliberately omits:

- extraction prompts and raw Markdown;
- MCP query arguments/results;
- node/edge property values;
- journal payloads;
- API keys/tokens; and
- unredacted third-party exception details that look like credentials or home
  paths.

Logs still contain operational metadata such as component names, event types,
counts, versions, and redacted paths. Keep the data home and backup remote
private. Never paste `config.yaml`, raw docs, `facts.jsonl`, cache, or journals
into a public issue.

## One-command support package

The default support command prints a Markdown report and creates its matching
redacted ZIP:

```bash
lorekeep support
# ...copy/paste-ready report...
# support bundle: /path/to/lorekeep-support-<timestamp>.zip
# sha256: ...
```

Variants:

```bash
lorekeep support --output ./support.zip
lorekeep support --report-only
lorekeep support --no-print --output ./support.zip
```

The ZIP is allowlisted to:

- `report.md`;
- the last 1,000 runtime-log lines after another redaction pass; and
- a manifest counter summary.

It does not contain config, raw data, facts, wiki, cache, or journals. Review the
report and attachment before publishing them anyway. Hidden `support report` and
`support bundle` entry points exist only as internal compatibility aliases; use
the combined command/options above.

## Automatic GitHub issue reporting

The default config enables a best-effort logging handler for unique
ERROR/CRITICAL signatures. It creates at most one issue per
`event + error type + component`; later occurrences increment local dedup state
instead of opening duplicates. Network/API failure never crashes Lorekeep and is
not marked successful, allowing a later retry.

Control and inspect it with:

```bash
lorekeep support status
lorekeep support on
lorekeep support off
```

Configuration:

```yaml
bugreport:
  enabled: true
  repo: manhhailua/lorekeep
  token_env: LOREKEEP_GITHUB_TOKEN
  labels: [auto-reported]
```

Token resolution order is:

1. the environment variable named by `bugreport.token_env`;
2. `GITHUB_TOKEN`; then
3. `gh auth token` when GitHub CLI is installed and authenticated.

Use a fine-grained token with issue-write permission only for the configured
repository. Automatic issue bodies pass through the same privacy redaction and
contain runtime metadata/trace locations rather than knowledge payloads.

Dedup state lives at `logs/bugreport-dedup.json`. `support status` reports token
resolution, issue count, and total occurrences without printing the token.

## Daemon and service logs

The unified runtime log is authoritative. Platform launchers can also retain
small bootstrap logs for failures before Lorekeep configures logging:

- systemd user service: `journalctl --user-unit lorekeep --since today`;
- launchd: `<data-home>/logs/daemon-bootstrap.log` and `.err.log`; and
- the background process started by interactive `init`:
  `<data-home>/logs/daemon-bootstrap.log`.

Old installations can retain `agent.log`, `daemon.log`, or `daemon.err.log` in
the data-home root. Support reports mention only their metadata/status and never
place their contents in the ZIP.

## First-response checklist

```bash
lorekeep doctor
lorekeep agent detect
lorekeep agent service status
lorekeep support
```

For MCP stdio startup, waiting is expected. A clean timeout with no traceback is
a boot smoke, not a complete protocol test:

```bash
timeout 3 lorekeep serve --transport stdio </dev/null
```

For compile errors, preserve the generated support bundle and manifest counters,
but report raw document excerpts only after deliberate manual redaction.

## Related

- [Getting started](getting-started.md)
- [Serving over MCP](serve.md)
- [Backing up and syncing](backup.md)
