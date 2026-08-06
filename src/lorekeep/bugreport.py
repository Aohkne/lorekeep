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
import traceback as _tb
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("lorekeep.bugreport")

# Warn-once flag for the "no token" case (per-process).
_warned_no_token = False


# ---------------------------------------------------------------------------
# Token resolution (fallback chain)
# ---------------------------------------------------------------------------

def _gh_cli_token() -> str:
    """Try to read a token from the installed ``gh`` CLI, or empty string."""
    import shutil
    import subprocess
    if not shutil.which("gh"):
        return ""
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _resolve_token(token_env: str) -> str:
    """Resolve a GitHub token via a fallback chain.

    Priority:
    1. The env var named in ``token_env`` (explicit, user-controlled)
    2. ``GITHUB_TOKEN`` (GitHub Actions / CI standard)
    3. ``gh auth token`` output (if the user already ran ``gh auth login``)
    """
    token = os.environ.get(token_env, "")
    if token:
        return token
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    return _gh_cli_token()


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

    # Full traceback with redacted exception message — same pattern as
    # _SafeFileFormatter in output.py.  Stack frames (file/line/function) are
    # safe to include and essential for diagnosing auto-reported errors.
    # Source code lines are stripped from each frame to avoid leaking
    # exception messages embedded in raise statements.
    msg = redact_text(record.getMessage())
    if record.exc_info and record.exc_info[2]:
        exc_type = record.exc_info[0]
        frames = "".join(
            f'  File "{f.filename}", line {f.lineno}, in {f.name}\n'
            for f in _tb.extract_tb(record.exc_info[2])
        )
        log_entry = (
            f"{msg}\n"
            f"{frames}{exc_type.__module__}.{exc_type.__name__}: [details redacted]"
        )
        log_entry = redact_text(log_entry)
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

        token = _resolve_token(br.token_env)
        if not token:
            if not _warned_no_token:
                _warned_no_token = True
                logger.warning(
                    "auto bug-report skipped: no GitHub token found "
                    "(checked %s, GITHUB_TOKEN, and gh auth); "
                    "set one or run `lorekeep bugreport off` to silence",
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
