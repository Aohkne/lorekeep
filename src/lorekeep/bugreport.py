"""Auto-report runtime errors as GitHub issues.

A logging handler that creates a GitHub issue the first time it sees a unique
ERROR/CRITICAL record.  Subsequent occurrences of the same error signature are
counted in the dedup file but do not create new issues.

Design properties:
- **Zero new dependencies** — uses ``urllib.request`` (stdlib).
- **Best-effort** — network failure never crashes lorekeep.
- **Privacy-safe** — every field runs through ``redact_text()`` before it
  leaves the machine.
- **Lazy** — the handler reads config/token on first ``emit()``, not at
  attach time, so ``output.configure_logging`` stays config-agnostic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("lorekeep.bugreport")

# Warn-once flag for the "no token" case (per-process).
_warned_no_token = False


# ---------------------------------------------------------------------------
# Dedup persistence
# ---------------------------------------------------------------------------

def _dedup_path() -> Path:
    """Return the path to the dedup state file."""
    from lorekeep.paths import resolve_paths
    return resolve_paths()["logs"] / "bugreport-dedup.json"


def _load_dedup(path: Path) -> dict[str, dict]:
    """Load the dedup state (signature → metadata)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_dedup(path: Path, data: dict[str, dict]) -> None:
    """Atomically save the dedup state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

def _signature(event: str, error_type: str, component: str) -> str:
    """Stable short hash identifying a unique error class."""
    raw = f"{event}|{error_type}|{component}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def _create_github_issue(
    repo: str,
    token: str,
    title: str,
    body: str,
    labels: list[str],
) -> int | None:
    """Create a GitHub issue and return its number, or ``None`` on failure."""
    url = f"https://api.github.com/repos/{repo}/issues"
    payload = json.dumps({
        "title": title,
        "body": body,
        "labels": labels,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("number")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        logger.debug("github issue creation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Issue body builder
# ---------------------------------------------------------------------------

def _build_issue_body(record: logging.LogRecord, run_id: str) -> str:
    """Build a privacy-safe Markdown issue body from a log record."""
    from lorekeep import __version__
    from lorekeep.redaction import redact_text

    event = getattr(record, "event", "runtime")
    component = record.name
    level = record.levelname
    error_type = ""
    if record.exc_info and record.exc_info[0]:
        error_type = record.exc_info[0].__name__

    # Redacted log message.  Traceback frames are NOT included — they can
    # carry the exception message in the source line of the raise statement.
    # The full redacted traceback is already in the runtime log; the issue
    # body only needs enough metadata to identify the error class.
    msg = redact_text(record.getMessage())
    if record.exc_info and record.exc_info[0]:
        exc_type = record.exc_info[0]
        log_entry = f"{msg}\n{exc_type.__module__}.{exc_type.__name__}: [details redacted]"
    else:
        log_entry = msg
    log_entry = redact_text(log_entry)

    return (
        "## Auto-reported error\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| Event | `{redact_text(event)}` |\n"
        f"| Level | {level} |\n"
        f"| Component | `{redact_text(component)}` |\n"
        f"| Run ID | {redact_text(run_id)} |\n"
        f"| Version | {__version__} |\n"
        f"| Platform | {redact_text(platform.platform())} |\n"
        f"| Python | {redact_text(platform.python_version())} |\n"
        f"{f'| Error type | `{error_type}` |' + chr(10) if error_type else ''}"
        "\n"
        "<details><summary>Redacted log entry</summary>\n\n"
        f"```\n{log_entry}\n```\n\n"
        "</details>\n\n"
        "---\n"
        "*Created automatically by `lorekeep` runtime diagnostics. "
        "Sensitive data has been redacted.*\n"
    )


# ---------------------------------------------------------------------------
# Logging handler
# ---------------------------------------------------------------------------

class BugReportHandler(logging.Handler):
    """Create a GitHub issue on the first occurrence of each unique error."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        """Process a single log record (best-effort — never raises)."""
        try:
            self._handle(record)
        except Exception:
            # Never let the handler crash the caller.
            pass

    def _handle(self, record: logging.LogRecord) -> None:
        global _warned_no_token

        if record.levelno < logging.ERROR:
            return

        # Lazily load config.
        from lorekeep.config import load_config
        from lorekeep.paths import resolve_paths

        p = resolve_paths()
        cfg = load_config(p["config"])
        br = cfg.bugreport

        if not br.enabled:
            return

        token = os.environ.get(br.token_env, "")
        if not token:
            if not _warned_no_token:
                _warned_no_token = True
                logger.warning(
                    "auto bug-report skipped: env %s is not set; "
                    "set it or run `lorekeep bugreport off` to silence",
                    br.token_env,
                )
            return

        event = getattr(record, "event", "runtime")
        component = record.name
        error_type = ""
        if record.exc_info and record.exc_info[0]:
            error_type = record.exc_info[0].__name__
        sig = _signature(event, error_type, component)

        # Dedup check.
        dpath = _dedup_path()
        dedup = _load_dedup(dpath)
        if sig in dedup:
            dedup[sig]["count"] = dedup[sig].get("count", 1) + 1
            _save_dedup(dpath, dedup)
            return

        # Build and submit issue.
        from lorekeep.output import _run_id
        run_id = _run_id
        title = f"[auto] {event}" + (f" ({error_type})" if error_type else "")
        body = _build_issue_body(record, run_id)

        issue_num = _create_github_issue(br.repo, token, title, body, list(br.labels))
        if issue_num is not None:
            dedup[sig] = {
                "issue_number": issue_num,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "count": 1,
            }
            _save_dedup(dpath, dedup)
            logger.info(
                "auto bug-report created issue #%d for %s",
                issue_num, sig,
            )
        # On failure: don't record in dedup so the next run can retry.
