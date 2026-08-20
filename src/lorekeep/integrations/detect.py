"""Detect coding agents: active session (env vars) + installed (filesystem markers).

Two-layer detection:
  1. ``detect_active_agent`` — which agent shell are we running inside right now?
     Uses well-known env vars.  Returns at most one agent name (or ``None``).
  2. ``detect_installed_agents`` — which agents are installed on this machine?
     Checks for config directories / binaries.  Returns a list (may be empty).

``detect_agents`` combines both: every installed agent is returned, with the
active one first.  Lorekeep aggregates knowledge across agents, so running
``init`` inside one agent's shell must still wire the others.

All per-agent facts come from :mod:`lorekeep.integrations.registry`.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from lorekeep.integrations.registry import AGENT_NAMES, all_specs

SUPPORTED_AGENTS = AGENT_NAMES


def _env_truthy(name: str) -> bool:
    val = os.environ.get(name, "")
    return val not in ("", "0", "false", "False")


def resolve_agent_markers(spec, markers: tuple[str, ...]) -> tuple[Path, ...]:
    """Resolve registry markers through an agent's custom-home variable."""
    custom = os.environ.get(spec.home_env) if spec.home_env else None
    if not custom or not spec.install_markers:
        return tuple(Path(marker).expanduser() for marker in markers)

    default_root = Path(spec.install_markers[0]).expanduser()
    custom_root = Path(custom).expanduser()
    resolved: list[Path] = []
    for marker in markers:
        path = Path(marker).expanduser()
        try:
            resolved.append(custom_root / path.relative_to(default_root))
        except ValueError:
            resolved.append(path)
    return tuple(resolved)


def detect_active_agent() -> str | None:
    """Return the agent whose shell we are running inside, or ``None``."""
    for spec in all_specs():
        if any(_env_truthy(v) for v in spec.active_env):
            return spec.name
    return None


def detect_installed_agents() -> list[str]:
    """Return all agents detected on this machine (filesystem + PATH)."""
    found: list[str] = []
    for spec in all_specs():
        if any(path.exists() for path in resolve_agent_markers(
            spec, spec.install_markers,
        )):
            found.append(spec.name)
            continue
        if any(shutil.which(b) for b in spec.binaries):
            found.append(spec.name)
    return found


def detect_agents() -> list[str]:
    """Return every installed agent, with the active one first.

    The active agent leads because it is the one the user is looking at, but it
    never excludes the others — a single knowledge graph needs every agent that
    can contribute to it wired in.
    """
    installed = detect_installed_agents()
    active = detect_active_agent()
    if active is None:
        return installed
    return [active, *(a for a in installed if a != active)]
