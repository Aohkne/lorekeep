"""Durable, fast ingress for coding-agent lifecycle hooks.

Native hook processes only normalize a small payload and atomically enqueue it.
The daemon later performs transcript I/O and compile work, so even Codex's
three-second ``SessionEnd`` budget is never spent importing knowledge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lorekeep.integrations.common import atomic_write

MAX_HOOK_PAYLOAD_BYTES = 256 * 1024
MAX_RETRY_DELAY_SECONDS = 300
# A permanently unresolvable event (transcript deleted, unreadable format)
# must not retry forever; it is dropped after this many attempts and the
# failure stays visible in `doctor` until then.
MAX_HOOK_RETRY_ATTEMPTS = 10
_UNSAFE_KEY_RE = re.compile(r"[^A-Za-z0-9._-]+")
log = logging.getLogger("lorekeep.hooks")


@dataclass(slots=True)
class HookEvent:
    agent: str
    trigger: str
    session_id: str
    transcript_path: str | None
    cwd: str | None
    native_event: str | None
    reason: str | None
    received_at: float
    attempts: int = 0
    last_attempt_at: float | None = None


@dataclass(frozen=True, slots=True)
class HookDrainReport:
    processed: int = 0
    written: int = 0
    deferred: int = 0
    failed: int = 0
    ignored: int = 0


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _payload_value(payload: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = _text(payload.get(name))
        if value:
            return value
    props = payload.get("properties")
    if isinstance(props, dict):
        for name in names:
            value = _text(props.get(name))
            if value:
                return value
    return None


def parse_hook_payload(raw: str) -> dict[str, Any]:
    """Parse a bounded native-hook JSON object; empty stdin is valid."""
    if len(raw.encode("utf-8")) > MAX_HOOK_PAYLOAD_BYTES:
        raise ValueError("hook payload exceeds 256 KiB")
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("hook payload must be one JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be one JSON object")
    return payload


def _event_key(agent: str, session_id: str, payload: dict[str, Any]) -> str:
    raw = session_id or hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    safe = _UNSAFE_KEY_RE.sub("-", raw).strip("-") or "unknown"
    if len(safe) > 96:
        suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:80]}-{suffix}"
    return f"{agent}-{safe}"


def enqueue_hook_event(
    home: Path,
    *,
    agent: str,
    trigger: str,
    raw_payload: str = "",
    session_id: str | None = None,
    cwd: str | None = None,
    now: float | None = None,
) -> Path:
    """Normalize one native hook invocation into a coalescing event file."""
    from lorekeep.integrations.registry import get

    spec = get(agent)
    if spec.hook is None:
        raise ValueError(f"{agent} has no lifecycle hook")
    if trigger != spec.hook.trigger:
        raise ValueError(
            f"{agent} hook trigger must be {spec.hook.trigger}, got {trigger}"
        )

    payload = parse_hook_payload(raw_payload)
    normalized_session = (
        _text(session_id)
        or _payload_value(
            payload, "session_id", "sessionId", "conversation_id",
            "conversationId", "id", "sessionID",
        )
        or ""
    )
    normalized_cwd = _text(cwd) or _payload_value(
        payload, "cwd", "working_directory", "workingDirectory"
    )
    event = HookEvent(
        agent=agent,
        trigger=trigger,
        session_id=normalized_session,
        transcript_path=_payload_value(
            payload, "transcript_path", "transcriptPath"
        ),
        cwd=normalized_cwd,
        native_event=_payload_value(
            payload, "hook_event_name", "hookEventName", "event"
        ) or spec.hook.event,
        reason=_payload_value(payload, "reason", "stop_reason", "stopReason"),
        received_at=time.time() if now is None else now,
    )

    event_root = Path(home) / "hook-events"
    event_dir = event_root / agent
    for directory in (event_root, event_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(directory, 0o700)
    path = event_dir / f"{_event_key(agent, normalized_session, payload)}.json"
    atomic_write(
        path, json.dumps(asdict(event), sort_keys=True) + "\n", mode=0o600,
    )
    return path


def _load_event(path: Path) -> HookEvent:
    data = json.loads(path.read_text(encoding="utf-8"))
    return HookEvent(**data)


def _retry_ready(event: HookEvent, now: float) -> bool:
    if event.last_attempt_at is None:
        return True
    delay = min(MAX_RETRY_DELAY_SECONDS, 2 ** min(event.attempts, 8))
    return now - event.last_attempt_at >= delay


def drain_hook_events(
    home: Path,
    raw_root: Path,
    agents_config,
    *,
    now: float | None = None,
) -> HookDrainReport:
    """Import ready events and retain failures for bounded-backoff retry."""
    from lorekeep.importer.session_dump import dump_session_turns, prune_sessions
    from lorekeep.integrations.registry import find

    current = time.time() if now is None else now
    processed = written_count = deferred = failed = ignored = 0
    event_root = Path(home) / "hook-events"
    if not event_root.is_dir():
        return HookDrainReport()

    for path in sorted(event_root.glob("*/*.json")):
        try:
            event = _load_event(path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            log.warning(
                "invalid hook event path=%s error_type=%s",
                path.name, type(exc).__name__,
                extra={"event": "hook.event_invalid"},
            )
            failed += 1
            continue

        spec = find(event.agent)
        if (
            spec is None
            or spec.hook is None
            or event.agent not in agents_config.enabled
            or not agents_config.watch_transcripts
        ):
            path.unlink(missing_ok=True)
            ignored += 1
            continue

        if event.trigger != "session_end":
            age = current - event.received_at
            if age < agents_config.session_end_idle_seconds:
                deferred += 1
                continue
        if not _retry_ready(event, current):
            deferred += 1
            continue

        processed += 1
        try:
            if spec.session is None or spec.session_ns is None:
                raise RuntimeError(f"{event.agent} has no transcript importer")
            importer = spec.importer()
            if importer is None:
                raise RuntimeError(f"{event.agent} importer is unavailable")
            handle = getattr(importer, spec.session.hook_resolve)(asdict(event))
            if handle is None:
                raise FileNotFoundError(
                    f"no transcript found for {event.agent}:{event.session_id}"
                )
            turns = getattr(importer, spec.session.parse)(handle)
            if not turns:
                raise ValueError(
                    f"transcript has no importable turns for {event.agent}"
                )
            written = dump_session_turns(
                turns,
                raw_root,
                namespace=spec.session_ns,
                session_key=getattr(importer, spec.session.key)(handle),
                max_chars=agents_config.transcript_max_chars,
                max_batches=agents_config.transcript_max_batches,
            )
            prune_sessions(
                raw_root, spec.session_ns,
                retain=agents_config.transcript_retain_sessions,
            )
            written_count += len(written)
            path.unlink(missing_ok=True)
        except Exception as exc:
            event.attempts += 1
            event.last_attempt_at = current
            if event.attempts >= MAX_HOOK_RETRY_ATTEMPTS:
                path.unlink(missing_ok=True)
                log.warning(
                    "hook event dropped after retries agent=%s session=%s",
                    event.agent, event.session_id,
                    extra={"event": "hook.event_dropped"},
                )
                failed += 1
                continue
            atomic_write(
                path,
                json.dumps(asdict(event), sort_keys=True) + "\n",
                mode=0o600,
            )
            log.warning(
                "hook event import failed agent=%s attempts=%s error_type=%s",
                event.agent, event.attempts, type(exc).__name__,
                extra={"event": "hook.event_import_failed"},
            )
            failed += 1

    return HookDrainReport(
        processed=processed,
        written=written_count,
        deferred=deferred,
        failed=failed,
        ignored=ignored,
    )
