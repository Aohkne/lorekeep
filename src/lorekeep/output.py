"""Colored, terminal-aware output for the lorekeep CLI.

Single chokepoint for all ``rich`` interaction. The module-level
:class:`~rich.console.Console` auto-strips color in a non-tty (tests via
CliRunner, the daemon bootstrap-log redirect), so plain-text contracts hold
and ANSI never leaks into captured output or log files.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

if TYPE_CHECKING:
    from collections.abc import Iterator

console = Console()
stderr_console = Console(stderr=True)

_quiet = False
_logging_configured = False
_file_handler: logging.Handler | None = None
_run_id = uuid.uuid4().hex[:12]
_exception_hooks_configured = False


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler that reapplies private permissions after rollover."""

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


class _SafeFileFormatter(logging.Formatter):
    """Plain UTC formatter that redacts the fully rendered traceback."""

    converter = __import__("time").gmtime

    def formatException(self, ei) -> str:
        """Keep stack frames and exception type, but never exception content."""
        exc_type, _exc_value, tb = ei
        frames = "".join(traceback.format_tb(tb))
        return f"{frames}{exc_type.__module__}.{exc_type.__name__}: [details redacted]"

    def format(self, record: logging.LogRecord) -> str:
        from lorekeep.redaction import redact_text
        return redact_text(super().format(record))


class _RuntimeContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id
        return True


def runtime_log_path() -> Path:
    """Return the resolved unified runtime log path without creating it."""
    from lorekeep.paths import resolve_paths
    return resolve_paths()["logs"] / "lorekeep.log"


def _make_file_handler(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    handler = _PrivateRotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    handler.setFormatter(_SafeFileFormatter(
        "%(asctime)sZ level=%(levelname)s component=%(name)s "
        "event=%(event)s pid=%(process)d run_id=%(run_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        defaults={"event": "runtime"},
    ))
    handler.addFilter(_RuntimeContextFilter())
    return handler


def _install_exception_hooks() -> None:
    """Record otherwise-unhandled main-thread and worker-thread failures."""
    global _exception_hooks_configured
    if _exception_hooks_configured:
        return
    previous_sys_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def _sys_hook(exc_type, exc_value, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous_sys_hook(exc_type, exc_value, tb)
            return
        logging.getLogger("lorekeep.runtime").critical(
            "unhandled exception error_type=%s", exc_type.__name__,
            exc_info=(exc_type, exc_value, tb), extra={"event": "runtime.unhandled"},
        )

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, KeyboardInterrupt):
            previous_thread_hook(args)
            return
        logging.getLogger("lorekeep.runtime").critical(
            "unhandled thread exception error_type=%s thread=%s",
            args.exc_type.__name__, args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            extra={"event": "runtime.thread_unhandled"},
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    _exception_hooks_configured = True


def is_quiet() -> bool:
    """True when ``--quiet``/WARNING level suppresses progress output."""
    return _quiet


def is_terminal() -> bool:
    """Whether stdout is a tty (color + progress bars). False under CliRunner/daemon."""
    return console.is_terminal


# ── status-line helpers ──────────────────────────────────────────────────────
# The message is escaped so a caller's ``[``/`]`` (e.g. namespaces lists) is
# never misread as Rich markup. After color-strip the plain text is verbatim,
# so substring assertions ("all checks passed", "backup failed", …) survive.

def ok(msg: str) -> None:
    console.print(f"[green]✓[/green] {escape(msg)}")


def info(msg: str) -> None:
    console.print(f"[cyan]→[/cyan] {escape(msg)}")


def step(msg: str) -> None:
    console.print(f"[dim]…[/dim] {escape(msg)}")


def dim(msg: str) -> None:
    console.print(f"[dim]{escape(msg)}[/dim]")


def warn(msg: str) -> None:
    # stdout (not stderr): lorekeep's status tests assert on result.stdout, and
    # the pre-existing doctor/check/backup FAIL lines were plain stdout echos.
    console.print(f"[yellow]![/yellow] {escape(msg)}")


def error(msg: str) -> None:
    console.print(f"[red]✗[/red] {escape(msg)}")


# ── progress / spinner ───────────────────────────────────────────────────────

class _NullProgress:
    """No-op handle used when there's no tty (tests, daemon log). Falsy."""

    def __bool__(self) -> bool:
        return False

    def advance(self, n: int = 1) -> None:
        pass

    def update(self, completed: int | None = None, total: int | None = None) -> None:
        pass


class _ProgressHandle:
    def __init__(self, bar, task) -> None:
        self._bar = bar
        self._task = task

    def __bool__(self) -> bool:
        return True

    def advance(self, n: int = 1) -> None:
        self._bar.advance(self._task, n)

    def update(self, completed: int | None = None, total: int | None = None) -> None:
        kwargs: dict[str, int] = {}
        if completed is not None:
            kwargs["completed"] = completed
        if total is not None:
            kwargs["total"] = total
        if kwargs:
            self._bar.update(self._task, **kwargs)


@contextmanager
def progress(description: str, total: int | None = None) -> Iterator[_NullProgress | _ProgressHandle]:
    """A Rich Progress bar in a tty; a silent no-op elsewhere.

    Hard-gated on ``console.is_terminal`` so progress text can never appear in
    CliRunner capture or daemon bootstrap logs (belt-and-suspenders on top of Rich's
    own auto-disable in a non-tty).
    """
    if not console.is_terminal:
        yield _NullProgress()
        return
    from rich.progress import BarColumn, Progress, TextColumn
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
        transient=True,
    ) as bar:
        task = bar.add_task(description, total=total)
        yield _ProgressHandle(bar, task)


@contextmanager
def status(label: str) -> Iterator[None]:
    """A Rich Status spinner in a tty; a single plain line elsewhere."""
    if not console.is_terminal:
        console.print(escape(label))
        yield
        return
    from rich.status import Status
    with Status(escape(label), console=console, spinner="dots"):
        yield


# ── logging ──────────────────────────────────────────────────────────────────

def configure_logging(level: int = logging.INFO) -> None:
    """Attach a colored ``RichHandler`` to the root logger; set the lorekeep level.

    The handler goes on *root* (not just ``lorekeep``) so litellm WARNING records
    keep their current stderr visibility — reformatted only, no level change, so
    no new INFO/DEBUG noise. ``propagate`` stays True (pytest ``caplog`` depends
    on it). Idempotent: the handler is attached once per process.
    """
    global _quiet, _logging_configured, _file_handler
    _quiet = level >= logging.WARNING
    lorekeep_logger = logging.getLogger("lorekeep")
    lorekeep_logger.setLevel(level)
    if _file_handler is None:
        try:
            _file_handler = _make_file_handler(runtime_log_path())
            lorekeep_logger.addHandler(_file_handler)
        except OSError as exc:
            # Logging must never make the CLI unusable (read-only homes, full
            # disks, and restrictive containers are all legitimate runtimes).
            logging.getLogger(__name__).debug("file logging unavailable: %s", exc)
    elif _file_handler not in lorekeep_logger.handlers:
        lorekeep_logger.addHandler(_file_handler)
    if _file_handler is not None:
        _file_handler.setLevel(level)
    _install_exception_hooks()
    if _logging_configured:
        return
    from rich.logging import RichHandler
    logging.getLogger().addHandler(RichHandler(
        console=stderr_console,
        show_time=False,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    ))
    _logging_configured = True
