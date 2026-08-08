"""Contracts that keep active documentation aligned with the runtime surface."""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

from lorekeep.config import load_config
import lorekeep.mcp_server as mcp_server


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _active_markdown() -> list[Path]:
    files = [ROOT / "README.md"]
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS)
        if rel.parts[0] in {"superpowers", "archive"}:
            continue
        if rel == Path("cli-consistency-review.md"):
            continue
        files.append(path)
    return files


def _slug(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return re.sub(r"\s+", "-", text)


def _anchors(path: Path) -> set[str]:
    counts: dict[str, int] = {}
    result: set[str] = set()
    for heading in HEADING_RE.findall(path.read_text(encoding="utf-8")):
        base = _slug(heading)
        count = counts.get(base, 0)
        counts[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def test_cli_reference_is_generated_from_current_typer_app() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate_cli_reference.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_active_markdown_local_links_and_anchors_resolve() -> None:
    failures: list[str] = []
    for source in _active_markdown():
        text = source.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            target = target.split(" ", 1)[0]
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
                continue
            file_part, separator, anchor = target.partition("#")
            destination = source if not file_part else (source.parent / unquote(file_part)).resolve()
            if not destination.exists():
                failures.append(f"{source.relative_to(ROOT)} -> missing {target}")
                continue
            if separator and anchor and destination.suffix.lower() == ".md":
                if unquote(anchor).lower() not in _anchors(destination):
                    failures.append(f"{source.relative_to(ROOT)} -> missing anchor {target}")
    assert not failures, "\n".join(failures)


def test_active_docs_do_not_advertise_removed_commands() -> None:
    patterns = {
        "lorekeep check": r"\blorekeep\s+check\b",
        "lorekeep bugreport": r"\blorekeep\s+bugreport\b",
        "lorekeep agent daemon": r"\blorekeep\s+agent\s+daemon\b",
        "lorekeep agent evolve": r"\blorekeep\s+agent\s+evolve\b",
    }
    failures: list[str] = []
    for path in _active_markdown():
        text = path.read_text(encoding="utf-8")
        for label, pattern in patterns.items():
            if re.search(pattern, text):
                failures.append(f"{path.relative_to(ROOT)} advertises removed `{label}`")
    assert not failures, "\n".join(failures)


def test_mcp_reference_names_the_exact_runtime_surface() -> None:
    reference = (DOCS / "architecture/serve-mcp.md").read_text(encoding="utf-8")
    tools = asyncio.run(mcp_server.mcp.list_tools())
    resources = asyncio.run(mcp_server.mcp.list_resources())

    assert len(tools) == 7
    for tool in tools:
        assert f"`{tool.name}" in reference
    for resource in resources:
        assert f"`{resource.uri}`" in reference
    assert "pending count is not namespace-filtered" in reference


def test_config_example_validates_against_current_model() -> None:
    config = load_config(ROOT / ".lorekeep/config.yaml.example")
    assert config.provider.model
    assert config.ns.personal_namespace
    assert set(config.agents.enabled) == {"claude", "cursor", "codex", "opencode"}


def test_backup_guide_says_pending_journals_are_durable() -> None:
    guide = (DOCS / "guides/backup.md").read_text(encoding="utf-8")
    assert "`pending/` journals" in guide
    assert "`pending/` (transient" not in guide


def test_historical_docs_are_explicitly_labeled() -> None:
    review = (DOCS / "cli-consistency-review.md").read_text(encoding="utf-8")
    archive_index = (DOCS / "superpowers/README.md").read_text(encoding="utf-8")
    plan = (
        DOCS / "superpowers/plans/2026-06-26-lorekeep-dotdir-backup.md"
    ).read_text(encoding="utf-8")
    spec = (
        DOCS / "superpowers/specs/2026-06-26-lorekeep-dotdir-layout-design.md"
    ).read_text(encoding="utf-8")
    assert "Historical document" in review
    assert "Historical implementation material" in archive_index
    assert "Historical implementation plan" in plan
    assert "Historical design" in spec
