"""Tolerant JSONL conversation parser for agent-owned local transcripts."""
from __future__ import annotations

import json
from pathlib import Path

from lorekeep.importer.claude import ConversationTurn, clean_user_message


_NON_TEXT_BLOCKS = {
    "reasoning", "thinking", "tool", "tool_call", "tool_result", "tool_use",
}


def _block_type(content: dict) -> str:
    return str(content.get("type") or "").lower().replace("-", "_")


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        kind = _block_type(content)
        if kind in _NON_TEXT_BLOCKS:
            return ""
        for key in ("text", "content", "value"):
            value = content.get(key)
            if isinstance(value, str):
                return value
        return ""
    if isinstance(content, list):
        parts = [_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    return ""


def _tools(record: dict, message: dict) -> list[str]:
    names: list[str] = []
    for owner in (record, message):
        calls = (
            owner.get("tool_calls")
            or owner.get("toolCalls")
            or owner.get("toolRequests")
            or []
        )
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function")
                name = call.get("name") or call.get("tool") or call.get("toolName")
                if isinstance(fn, dict):
                    name = name or fn.get("name")
                if isinstance(name, str) and name and name not in names:
                    names.append(name)
        content = owner.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if _block_type(block) in {"tool_use", "tool_call", "tool"}:
                    name = block.get("name") or block.get("tool") or block.get("toolName")
                    if isinstance(name, str) and name and name not in names:
                        names.append(name)
    return names


def parse_role_jsonl(path: Path) -> list[ConversationTurn]:
    """Parse user/assistant records across Qoder/Command Code variants."""
    turns: list[ConversationTurn] = []
    current_user: str | None = None
    assistants: list[str] = []
    tools: list[str] = []

    def flush() -> None:
        nonlocal current_user, assistants, tools
        if current_user is not None:
            turns.append(ConversationTurn(
                user_content=clean_user_message(current_user),
                assistant_text="\n\n".join(text for text in assistants if text),
                tool_calls=list(tools),
            ))
        current_user, assistants, tools = None, [], []

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        raw_message = record.get("message")
        message = raw_message if isinstance(raw_message, dict) else record
        metadata = record.get("metadata")
        if record.get("isMeta") is True or (
            isinstance(metadata, dict) and metadata.get("isAutomated") is True
        ):
            continue
        role = record.get("role") or message.get("role")
        record_type = str(record.get("type") or "").lower()
        if not role and record_type in {"user", "human"}:
            role = "user"
        elif not role and record_type in {"assistant", "ai", "model"}:
            role = "assistant"

        content = message.get("content")
        if content is None and raw_message is not None and not isinstance(raw_message, dict):
            content = raw_message
        if content is None:
            content = record.get("content") or record.get("text")
        text = _text(content)

        if role in {"user", "human"}:
            # Qoder/Command Code persist tool results as user-role records.
            # They are execution noise, not a new human question.
            if not text:
                continue
            flush()
            current_user = text
        elif role in {"assistant", "ai", "model"}:
            if text:
                assistants.append(text)
            for name in _tools(record, message):
                if name not in tools:
                    tools.append(name)

    flush()
    return turns
