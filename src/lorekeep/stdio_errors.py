"""Classify MCP stdio shutdown vs real serve failures.

When a coding agent on Windows (and sometimes Unix) closes the MCP pipe,
``mcp.server.stdio``'s anyio TaskGroup exits with ``ExceptionGroup`` wrapping
``ClosedResourceError`` / ``BrokenResourceError``. That is a client disconnect,
not a Lorekeep bug -- logging it as ``mcp.failed`` then re-raising also fires
``runtime.unhandled`` and auto-opens duplicate GitHub issues.
"""
from __future__ import annotations

import errno
import sys


def _anyio_disconnect_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    try:
        import anyio

        for name in ("ClosedResourceError", "BrokenResourceError", "EndOfStream"):
            cls = getattr(anyio, name, None)
            if isinstance(cls, type):
                types.append(cls)
    except ImportError:
        pass
    return tuple(types)


_DISCONNECT: tuple[type[BaseException], ...] = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    EOFError,
    *_anyio_disconnect_types(),
)


def is_client_disconnect(exc: BaseException) -> bool:
    """True when *exc* (or every ExceptionGroup member) is a closed stdio pipe."""
    if isinstance(exc, _DISCONNECT):
        return True
    if isinstance(exc, OSError) and exc.errno in (
        errno.EPIPE,
        errno.ECONNRESET,
        errno.ECONNABORTED,
    ):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(
            is_client_disconnect(inner) for inner in exc.exceptions
        )
    return False


def prepare_windows_stdio_loop() -> None:
    """Use the selector loop on Windows so asyncio can wrap stdin/stdout pipes.

    The default ProactorEventLoop cannot ``add_reader`` on stdio, which is what
    anyio/mcp stdio transport needs. No-op on non-Windows.
    """
    if sys.platform != "win32":
        return
    import asyncio

    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:
        return
    asyncio.set_event_loop_policy(policy_cls())


def disconnect_error_types(exc: BaseException) -> list[str]:
    """Innermost exception type names -- for logs, never messages."""
    if isinstance(exc, BaseExceptionGroup):
        names: list[str] = []
        for inner in exc.exceptions:
            names.extend(disconnect_error_types(inner))
        return names
    return [type(exc).__name__]
