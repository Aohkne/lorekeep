"""Client-disconnect classification for MCP stdio (Windows auto-issues #271-#274)."""
from __future__ import annotations

import errno

import anyio
import pytest

from lorekeep.stdio_errors import (
    is_client_disconnect,
    prepare_windows_stdio_loop,
)


def test_closed_resource_is_disconnect():
    assert is_client_disconnect(anyio.ClosedResourceError())


def test_broken_pipe_is_disconnect():
    assert is_client_disconnect(BrokenPipeError())


def test_oserror_epipe_is_disconnect():
    assert is_client_disconnect(OSError(errno.EPIPE, "broken pipe"))


def test_valueerror_is_not_disconnect():
    assert is_client_disconnect(ValueError("nope")) is False


def test_exceptiongroup_of_closed_resource_is_disconnect():
    exc = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [anyio.ClosedResourceError()],
    )
    assert is_client_disconnect(exc)


def test_nested_exceptiongroup_of_broken_resource_is_disconnect():
    inner = ExceptionGroup("inner", [anyio.BrokenResourceError()])
    outer = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert is_client_disconnect(outer)


def test_exceptiongroup_with_real_bug_is_not_disconnect():
    exc = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [anyio.ClosedResourceError(), RuntimeError("boom")],
    )
    assert is_client_disconnect(exc) is False


def test_empty_exceptiongroup_is_not_disconnect():
    with pytest.raises(ValueError):
        ExceptionGroup("empty", [])


def test_prepare_windows_stdio_loop_is_noop_off_windows(monkeypatch):
    import asyncio

    monkeypatch.setattr("lorekeep.stdio_errors.sys.platform", "linux")
    called = []
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda p: called.append(p))
    prepare_windows_stdio_loop()
    assert called == []


def test_prepare_windows_stdio_loop_sets_selector_policy(monkeypatch):
    import asyncio

    monkeypatch.setattr("lorekeep.stdio_errors.sys.platform", "win32")
    set_policies: list[object] = []

    class FakePolicy:
        pass

    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FakePolicy, raising=False)
    monkeypatch.setattr(asyncio, "set_event_loop_policy", lambda p: set_policies.append(p))
    prepare_windows_stdio_loop()
    assert len(set_policies) == 1
    assert isinstance(set_policies[0], FakePolicy)
