"""Shared integration helpers: install command, memory snippet, safe JSON merge."""
from __future__ import annotations

import copy
import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger("lorekeep.integrations")


def resolve_command(
    install_source: str | None,
    subcommand: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Return (command, args) to launch a lorekeep subcommand.

    Defaults to ``serve --transport stdio``.  Pass ``subcommand`` for others
    (e.g. ``["hook"]``).
    """
    cmd_args = subcommand or ["serve", "--transport", "stdio"]
    if not install_source or install_source == "pypi":
        return ("uvx", ["lorekeep", *cmd_args])
    if install_source == "local":
        return ("lorekeep", cmd_args)
    return ("uvx", ["--from", install_source, "lorekeep", *cmd_args])


def resolve_hook_command(
    agent: str,
    trigger: str,
    home: Path,
) -> tuple[str, list[str]]:
    """Return a fast command for the lifecycle-hook ingress.

    Session-end hooks must return quickly (Codex allows at most three seconds),
    so they must not cold-start ``uvx``.  The interpreter running the wiring
    command already has lorekeep installed; invoking that exact interpreter is
    both faster and stable across shell ``PATH`` differences.
    """
    return (
        sys.executable,
        [
            "-m", "lorekeep.cli", "hook",
            "--agent", agent,
            "--trigger", trigger,
            "--home", str(home),
        ],
    )


def shell_join(command: str, args: list[str]) -> str:
    """Render an argv vector for hook formats that require one shell string."""
    argv = [command, *args]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def is_lorekeep_hook_handler(handler: object) -> bool:
    """Return whether a native hook handler is managed by lorekeep."""
    if not isinstance(handler, dict):
        return False
    pieces: list[str] = []
    for key in ("command", "bash", "powershell"):
        value = handler.get(key)
        if isinstance(value, str):
            pieces.append(value)
    args = handler.get("args")
    if isinstance(args, list):
        pieces.extend(str(arg) for arg in args)
    text = " ".join(pieces).lower().replace('"', "").replace("'", "")
    return "lorekeep.cli hook" in text or "lorekeep hook" in text


def remove_lorekeep_hook_groups(hooks: dict, event: str) -> None:
    """Remove only lorekeep-managed handlers from one native event."""
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return
    kept_groups: list[object] = []
    for group in groups:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        nested = group.get("hooks")
        if isinstance(nested, list):
            kept = [item for item in nested if not is_lorekeep_hook_handler(item)]
            if kept:
                updated = dict(group)
                updated["hooks"] = kept
                kept_groups.append(updated)
        elif not is_lorekeep_hook_handler(group):
            kept_groups.append(group)
    if kept_groups:
        hooks[event] = kept_groups
    else:
        hooks.pop(event, None)


def upsert_lorekeep_hook(
    data: dict,
    event: str,
    group: dict,
    *,
    remove_from: tuple[str, ...] = (),
) -> None:
    """Preserve third-party hooks while replacing lorekeep's own handler."""
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    for old_event in (*remove_from, event):
        remove_lorekeep_hook_groups(hooks, old_event)
    hooks.setdefault(event, []).append(group)


def agent_memory_snippet() -> str:
    return (
        "## Lorekeep knowledge base (MCP)\n"
        "Before answering architecture/code/domain questions, query Lorekeep:\n"
        "search(q) -> get_node(id) -> neighbors / temporal_query as needed.\n"
        "Use context() for ontology, visible namespaces, and graph freshness.\n"
        "Always cite `src` provenance. Knowledge is namespace-scoped - if a fact is\n"
        "missing, it may be outside your scope, not nonexistent. Use propose_change\n"
        "for facts/links/updates and review_note for contradictions or gaps.\n"
    )


def atomic_write(path: Path, text: str, *, mode: int | None = None) -> Path:
    """Replace ``path`` in one step, preserving its permission bits.

    User-scope targets like ``~/.claude.json`` are mode 600 and written live by
    the agent itself, so a truncate-then-write would expose a window where the
    agent reads a half-file — or where credentials become world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    final_mode = mode
    if final_mode is None and path.exists():
        final_mode = path.stat().st_mode & 0o777
    tmp = path.with_name(f".{path.name}.lorekeep-{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if final_mode is not None:
            # Apply permissions before replace so a sensitive new file is
            # never briefly visible with permissions derived from the umask.
            os.chmod(tmp, final_mode)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path


def merge_json_config(
    path: Path,
    mutate: Callable[[dict], None],
    *,
    reset_if_corrupt: bool = False,
) -> Path | None:
    """Apply ``mutate`` to a JSON config, writing only if something changed.

    Returns ``path`` when the file was rewritten, ``None`` when it was already
    correct.  The daemon re-checks wiring on a timer, so a no-op must not churn
    the mtime of a file an agent is watching.

    Comparison is on the *parsed* value, never on rendered bytes: agents write
    these files with their own key order and escaping, and comparing text would
    make lorekeep rewrite the file on every single pass.

    An unparseable file is left untouched unless ``reset_if_corrupt``.  Agents
    write these live, so a mid-write read must never cost the user their other
    MCP servers or their stored credentials.
    """
    data: dict = {}
    existed = path.exists()
    if existed:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            if not reset_if_corrupt:
                log.warning(
                    "skipping unparseable agent config path=%s", path.name,
                    extra={"event": "wire.config_unparseable"},
                )
                return None
            data = {}
        if not isinstance(data, dict):
            if not reset_if_corrupt:
                return None
            data = {}

    before = copy.deepcopy(data)
    mutate(data)
    if existed and data == before:
        return None

    return atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_text_if_changed(path: Path, text: str) -> Path | None:
    """Write ``text`` only when it differs from what is already on disk."""
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return None
        except OSError:
            pass
    return atomic_write(path, text)
