from __future__ import annotations

from pathlib import Path
import json

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict[str, object]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _run_commands(job: dict[str, object]) -> list[str]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return [
        step["run"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]


def test_release_please_updates_local_uv_lock_version() -> None:
    config = json.loads(
        (REPO_ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )

    assert config["packages"]["."]["extra-files"] == [
        {
            "type": "toml",
            "path": "uv.lock",
            "jsonpath": "$.package[?(@.name=='lorekeep')].version",
        }
    ]


def test_ci_never_repairs_the_committed_lockfile() -> None:
    workflow = _workflow("ci.yml")
    commands = _run_commands(workflow["jobs"]["test"])

    assert "uv lock --check" in commands
    assert "uv sync --locked" in commands
    assert "uv run --locked python scripts/check-release-integrity.py" in commands
    assert "uv run --locked pytest -q" in commands
    assert "uv sync" not in commands


def test_release_publish_is_atomic_and_has_no_follow_up_pr() -> None:
    workflow = _workflow("release-please.yml")
    jobs = workflow["jobs"]

    assert "sync-uv-lock" not in jobs
    assert workflow["concurrency"]["cancel-in-progress"] is False

    release_steps = jobs["release-please"]["steps"]
    release_action = next(step for step in release_steps if step.get("id") == "rp")
    assert release_action["with"]["token"] == "${{ secrets.RELEASE_PLEASE_TOKEN }}"

    commands = _run_commands(jobs["publish"])
    source_check = next(i for i, command in enumerate(commands) if "uv lock --check" in command)
    build = commands.index("uv build")
    artifact_check = next(
        i for i, command in enumerate(commands) if "--dist dist" in command
    )
    assert source_check < build < artifact_check


def test_auto_merge_is_limited_to_trusted_release_please_prs() -> None:
    path = WORKFLOWS / "auto-merge-release-please.yml"
    content = path.read_text(encoding="utf-8")
    workflow = _workflow(path.name)
    condition = workflow["jobs"]["auto-merge"]["if"]

    assert "release-please--" in condition
    assert "head.repo.full_name == github.repository" in condition
    assert "autorelease: pending" in condition
    assert "types: [opened, synchronize, labeled]" in content
    assert "gh pr merge --auto --merge" in content
    assert "secrets.RELEASE_PLEASE_TOKEN ||" not in content
