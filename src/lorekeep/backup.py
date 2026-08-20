"""Manual backup of the lorekeep data home to a private git repo.

The backup repo lives inside the data home (``.lorekeep/`` in dev mode). It
tracks durable inputs plus the latest compiled graph/wiki snapshot. Local
configuration, credentials, caches, indexes, logs, and transient files remain
device-local.

Generated snapshots are deliberately marked non-mergeable in Git. A graph,
manifest, and wiki produced by different compiles must never be line-merged
into a synthetic snapshot that no compile actually published.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_GITIGNORE_BEGIN = "# BEGIN lorekeep local-only state"
_GITIGNORE_END = "# END lorekeep local-only state"
_LOCAL_ONLY_PATTERNS = (
    "config.yaml",
    "cache.json",
    "fts.sqlite",
    "graph/fts.sqlite",
    "graph/*.tmp",
    ".wiki-build.tmp/",
    ".wiki-rollback.tmp/",
    "wiki/.obsidian/",
    "wiki/.trash/",
    "wiki/.DS_Store",
    "logs/",
    "hook-events/",
    ".daemon.pid",
    "*.lock",
)
_LEGACY_DERIVED_IGNORES = {
    "graph/facts.jsonl",
    "graph/manifest.json",
    "wiki/",
}

BACKUP_GITIGNORE = """\
# BEGIN lorekeep local-only state
config.yaml
cache.json
fts.sqlite
graph/fts.sqlite
graph/*.tmp
.wiki-build.tmp/
.wiki-rollback.tmp/
wiki/.obsidian/
wiki/.trash/
wiki/.DS_Store
logs/
hook-events/
.daemon.pid
*.lock
# END lorekeep local-only state
"""

_GITATTRIBUTES_BEGIN = "# BEGIN lorekeep generated snapshots"
_GITATTRIBUTES_END = "# END lorekeep generated snapshots"
BACKUP_GITATTRIBUTES = """\
# BEGIN lorekeep generated snapshots
# Never combine outputs from two independent compile publications line by line.
graph/** -merge
wiki/** -merge
# log.md is append-only human-readable history, so normal text merging is safe.
wiki/log.md merge
# END lorekeep generated snapshots
"""


class BackupError(RuntimeError):
    """Raised when a git operation during backup fails."""


def _git(args: list[str], cwd: Path) -> str:
    """Run git in `cwd`, returning stdout. Raise BackupError on non-zero exit.

    Inline user.email/user.name so commits work without a global git identity
    (important in CI and fresh machines).
    """
    proc = subprocess.run(
        [
            "git",
            "-c", "user.email=lorekeep@backup.local",
            "-c", "user.name=lorekeep backup",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BackupError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _commit(home: Path, prefix: str) -> bool:
    """Stage all and commit with an ISO-8601 UTC message. Return True if committed."""
    _git(["add", "-A"], home)
    staged = _git(["diff", "--cached", "--name-only"], home)
    if not staged:
        return False
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _git(["commit", "-q", "-m", f"{prefix} {ts}"], home)
    return True


def _replace_managed_block(
    text: str, *, begin: str, end: str, replacement: str,
) -> str:
    """Replace one Lorekeep-owned metadata block and preserve user content."""
    lines = text.splitlines()
    try:
        start = lines.index(begin)
        stop = lines.index(end, start) + 1
    except ValueError:
        base = text.rstrip()
        return f"{base}\n\n{replacement}" if base else replacement
    updated = lines[:start] + replacement.rstrip().splitlines() + lines[stop:]
    return "\n".join(updated).rstrip() + "\n"


def _migrate_gitignore(text: str) -> str:
    """Install current local-only rules without discarding custom ignores.

    Older Lorekeep versions ignored ``graph/`` outputs and ``wiki/``. Remove
    only those known obsolete rules, then replace/append the managed block.
    """
    managed_rules = _LEGACY_DERIVED_IGNORES | set(_LOCAL_ONLY_PATTERNS)
    filtered = "\n".join(
        line for line in text.splitlines()
        if line.strip() not in managed_rules
    )
    return _replace_managed_block(
        filtered,
        begin=_GITIGNORE_BEGIN,
        end=_GITIGNORE_END,
        replacement=BACKUP_GITIGNORE,
    )


def _prepare_repo_metadata(home: Path) -> None:
    """Migrate backup metadata and untrack device-local state safely."""
    ignore_path = home / ".gitignore"
    old_ignore = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    new_ignore = _migrate_gitignore(old_ignore)
    if new_ignore != old_ignore:
        ignore_path.write_text(new_ignore, encoding="utf-8")

    attributes_path = home / ".gitattributes"
    old_attributes = (
        attributes_path.read_text(encoding="utf-8")
        if attributes_path.exists() else ""
    )
    new_attributes = _replace_managed_block(
        old_attributes,
        begin=_GITATTRIBUTES_BEGIN,
        end=_GITATTRIBUTES_END,
        replacement=BACKUP_GITATTRIBUTES,
    )
    if new_attributes != old_attributes:
        attributes_path.write_text(new_attributes, encoding="utf-8")

    # ``.gitignore`` does not affect files already tracked by an older backup.
    # Remove local-only paths from the index while preserving working copies.
    _git([
        "rm", "-r", "--cached", "--ignore-unmatch", "--",
        "config.yaml", "cache.json", "fts.sqlite", "graph/fts.sqlite",
        ".wiki-build.tmp", ".wiki-rollback.tmp", "wiki/.obsidian",
        "wiki/.trash", "wiki/.DS_Store", "logs", "hook-events",
        ".daemon.pid",
    ], home)


def _remote_sha(home: Path) -> str | None:
    """SHA of the remote branch tracking the current local branch, or None."""
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], home)
    if not branch or branch == "HEAD":
        return None
    out = _git(["ls-remote", "origin", branch], home)
    return out.split()[0] if out.strip() else None


def _rebase_if_remote_exists(home: Path, remote_ref: str) -> None:
    """Rebase onto ``remote_ref`` (e.g. ``origin/main``) if it exists.

    Silently skips if the ref does not exist on the remote.
    """
    try:
        _git(["rev-parse", "--verify", f"refs/remotes/{remote_ref}"], home)
        _git(["rebase", remote_ref], home)
    except BackupError:
        pass


def _delete_remote_branch_if_exists(home: Path, branch: str) -> None:
    """Delete a remote branch if it exists. Failures are non-fatal."""
    try:
        ref = _git(["ls-remote", "--heads", "origin", branch], home)
        if ref.strip():
            _git(["push", "origin", "--delete", branch], home)
    except BackupError:
        pass


def _ensure_branch(home: Path, target: str = "main") -> None:
    """Ensure the local branch is *target*, migrating from legacy names.

    Handles the one-time rename from ``master`` (or any other name) to the
    configured branch.  Fetches remote history under both the old and new
    branch names to handle multi-device migration (device A may have already
    renamed and pushed, while device B is still on the old branch).  After
    migration the old remote branch is deleted to prevent divergence.
    """
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], home)
    if not current or current == "HEAD" or current == target:
        return

    _git(["fetch", "origin"], home)

    # Integrate any remote history under the old branch name (e.g. origin/master).
    _rebase_if_remote_exists(home, f"origin/{current}")

    # Rename the local branch.
    _git(["branch", "-M", target], home)

    # Integrate remote history under the new branch name — another device may
    # have already migrated and pushed to origin/<target>.
    _rebase_if_remote_exists(home, f"origin/{target}")

    # Push the renamed branch and set up tracking.
    _git(["push", "-u", "origin", target], home)

    # Clean up the old remote branch to prevent future divergence.
    _delete_remote_branch_if_exists(home, current)


# ── Durable conflict resolution ──────────────────────────────────────────

_MERGE_SYSTEM_PROMPT = (
    "You are a documentation merge assistant. Two versions of a markdown file "
    "conflicted during git rebase. Merge them into a single coherent document.\n\n"
    "Rules:\n"
    "1. Preserve ALL content from both versions — never delete information.\n"
    "2. When both versions have the same section heading with different content, "
    "combine the content under that heading.\n"
    "3. When a section appears in only one version, keep it as-is.\n"
    "4. Maintain markdown formatting and heading hierarchy.\n"
    "5. Output ONLY the merged document — no commentary, no explanation."
)


def _get_rebase_versions(home: Path, path: str) -> tuple[str, str]:
    """Get ours (remote) and theirs (local) text during an active rebase.

    During rebase: stage 2 = ours (upstream/remote), stage 3 = theirs (local).
    """
    ours = _git(["show", f":2:{path}"], home)
    theirs = _git(["show", f":3:{path}"], home)
    return ours, theirs


def _merge_json(home: Path, path: str) -> bool:
    """Deep-merge two JSON versions (union dict keys, remote wins on conflict)."""
    try:
        ours_raw, theirs_raw = _get_rebase_versions(home, path)
        ours = json.loads(ours_raw)
        theirs = json.loads(theirs_raw)
    except (json.JSONDecodeError, BackupError):
        return False

    # Start with local, overlay remote (remote wins on scalar conflicts).
    merged = {**theirs, **ours}

    # For dict-valued keys, do a shallow union so local-only entries survive.
    for key, val in list(merged.items()):
        if isinstance(val, dict):
            local_val = theirs.get(key, {})
            if isinstance(local_val, dict):
                merged[key] = {**local_val, **val}

    (home / path).write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _merge_jsonl(home: Path, path: str) -> bool:
    """Union two JSONL versions by entry_id (or full-line dedup)."""
    try:
        ours_raw, theirs_raw = _get_rebase_versions(home, path)
    except BackupError:
        return False

    seen_ids: set[str] = set()
    seen_lines: set[str] = set()
    merged: list[str] = []

    for raw_text in (ours_raw, theirs_raw):
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entry_id = obj.get("entry_id") or obj.get("id") or line
            except json.JSONDecodeError:
                entry_id = line

            if entry_id not in seen_ids and line not in seen_lines:
                seen_ids.add(entry_id)
                seen_lines.add(line)
                merged.append(line)

    if not merged:
        return False

    (home / path).write_text("\n".join(merged) + "\n", encoding="utf-8")
    return True


def _merge_markdown(home: Path, path: str, provider: object) -> bool:
    """LLM-assisted semantic merge of two markdown versions."""
    try:
        ours, theirs = _get_rebase_versions(home, path)
    except BackupError:
        return False

    user_msg = (
        f"=== REMOTE VERSION ===\n{ours}\n\n"
        f"=== LOCAL VERSION ===\n{theirs}\n\n"
        f"=== MERGED ==="
    )
    try:
        merged = provider.complete(system=_MERGE_SYSTEM_PROMPT, user=user_msg)
    except Exception:
        return False

    if not merged or not merged.strip():
        return False

    (home / path).write_text(merged, encoding="utf-8")
    return True


def _resolve_durable_file(
    home: Path, path: str, provider: object | None = None,
) -> bool:
    """Attempt to resolve a single durable conflict by file type.

    Returns True if resolved, False if unresolvable.
    """
    if path.endswith(".json"):
        return _merge_json(home, path)
    if path.endswith(".jsonl"):
        return _merge_jsonl(home, path)
    if path.endswith(".md"):
        if provider is None:
            return False
        return _merge_markdown(home, path, provider)
    return False  # unknown file type — never auto-merge


def _resolve_durable_conflicts(
    home: Path,
    durable_paths: list[str],
    provider: object | None = None,
) -> list[str]:
    """Attempt to resolve all durable conflicts. Return list of unresolved paths."""
    unresolved: list[str] = []
    for path in durable_paths:
        if _resolve_durable_file(home, path, provider):
            _git(["add", "--", path], home)
        else:
            unresolved.append(path)
    return unresolved


def _classify_conflicts(paths: str) -> tuple[list[str], list[str]]:
    """Split conflicted paths into snapshot vs durable.

    Snapshot paths (graph/, wiki/) are regenerable and safe to auto-resolve.
    Durable paths (raw/, schema.json, pending/) require manual merge.
    """
    snapshot = []
    durable = []
    for line in paths.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("graph/") or line.startswith("wiki/"):
            snapshot.append(line)
        else:
            durable.append(line)
    return snapshot, durable


def _resolve_snapshot_conflict_inplace(
    home: Path, *, durable_resolver=None,
) -> list[str]:
    """Resolve snapshot conflicts during an active rebase (in-place).

    Must be called while a rebase is in progress with unmerged paths.
    For each conflicted graph/wiki path: accepts the remote version
    (``checkout --ours`` during rebase, which is the upstream/remote),
    stages it. Returns the list of durable conflicts that could not be
    auto-resolved.

    If *durable_resolver* is provided, it is called with the durable paths
    and should return the subset it could not resolve.
    """
    conflicts = _git(["diff", "--name-only", "--diff-filter=U"], home)
    snapshot, durable = _classify_conflicts(conflicts)
    for path in snapshot:
        # During rebase: --ours = upstream (remote), --theirs = local commit
        _git(["checkout", "--ours", "--", path], home)
        _git(["add", "--", path], home)
    if durable and durable_resolver:
        durable = durable_resolver(home, durable)
    return durable


def _reconcile_remote(
    home: Path,
    *,
    auto_fix_snapshots: bool = False,
    durable_resolver=None,
) -> None:
    """Fetch + rebase, aborting cleanly rather than leaving a broken repo.

    The generated graph/wiki paths use ``-merge`` attributes. If two devices
    published different snapshots, Git stops instead of line-merging them.

    When *auto_fix_snapshots* is True (daemon mode), snapshot-only conflicts
    are auto-resolved by accepting the remote version. The daemon recompiles
    locally afterwards.

    When *durable_resolver* is provided, durable conflicts (raw/, schema.json,
    pending/) are passed to it for automatic resolution. Any durable conflicts
    it cannot resolve still require user intervention.
    """
    _git(["fetch", "origin"], home)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], home)
    if not branch or branch == "HEAD":
        return
    remote_ref = f"refs/remotes/origin/{branch}"
    try:
        _git(["rev-parse", "--verify", remote_ref], home)
    except BackupError:
        return  # first push: the remote has no matching branch yet
    try:
        _git(["rebase", f"origin/{branch}"], home)
    except BackupError as exc:
        # Rebase failed — we're mid-rebase with unmerged paths.
        try:
            durable = _resolve_snapshot_conflict_inplace(
                home, durable_resolver=durable_resolver,
            ) if auto_fix_snapshots else None
        except BackupError:
            durable = None

        if durable is not None and not durable:
            # All conflicts were snapshot-only and auto-resolved.
            # GIT_EDITOR=true skips the commit-message editor.
            import os
            env = dict(os.environ, GIT_EDITOR="true")
            proc = subprocess.run(
                [
                    "git",
                    "-c", "user.email=lorekeep@backup.local",
                    "-c", "user.name=lorekeep backup",
                    "rebase", "--continue",
                ],
                cwd=str(home),
                capture_output=True, text=True, env=env,
            )
            if proc.returncode != 0:
                raise BackupError(
                    f"git rebase --continue failed (exit {proc.returncode}): "
                    f"{proc.stderr.strip()}"
                )
            return

        # Either auto_fix disabled, or durable conflicts remain.
        # Abort to restore pre-rebase state and raise.
        try:
            _git(["rebase", "--abort"], home)
        except BackupError as abort_exc:
            raise BackupError(
                "backup rebase failed and could not be aborted; inspect the "
                f"repository at {home}: {abort_exc}"
            ) from exc

        if durable:
            paths = ", ".join(durable)
        else:
            try:
                conflicts = _git(["diff", "--name-only", "--diff-filter=U"], home)
            except BackupError:
                conflicts = ""
            paths = ", ".join(
                p for p in conflicts.splitlines() if p.strip()
            ) or "unknown paths"

        raise BackupError(
            "backup rebase conflict; local repository was restored. "
            f"Conflicts: {paths}. Merge durable raw/schema/pending inputs, "
            "then run `lorekeep compile` and `lorekeep backup`. Never "
            "line-merge graph/ or wiki/ generated snapshots."
        ) from exc


def has_remote(home: Path) -> bool:
    """Check if a backup remote (git repo + origin) is configured."""
    if not (home / ".git").is_dir():
        return False
    try:
        remotes = _git(["remote"], home)
        return "origin" in remotes.split()
    except BackupError:
        return False


def sync_backup(
    home: Path,
    *,
    auto_fix: bool = True,
    branch: str = "main",
    durable_resolver=None,
) -> bool:
    """Sync backup: pull --rebase from remote, then commit + push.

    Used by daemon after compile/resolve/heal. Pulls changes from other
    machines first (fetch + rebase), then pushes local changes.

    When *auto_fix* is True (default), snapshot conflicts on graph/wiki
    are auto-resolved by accepting the remote version. The daemon will
    recompile locally to align with merged durable inputs.

    *branch* is the configured backup branch name. On first run after
    upgrade, an existing repo on a legacy branch (e.g. ``master``) is
    migrated to this name.

    Silently returns False if:
    - No backup repo or remote configured
    - Network error or durable conflict (user resolves with ``lorekeep backup``)
    """
    if not has_remote(home):
        return False
    try:
        _prepare_repo_metadata(home)
        _commit(home, "backup")
        _ensure_branch(home, branch)
        before = _remote_sha(home)
        _reconcile_remote(
            home, auto_fix_snapshots=auto_fix, durable_resolver=durable_resolver,
        )
        _git(["push"], home)
        after = _remote_sha(home)
        return after != before
    except BackupError:
        return False


def init_backup(home: Path, remote: str, *, branch: str = "main") -> None:
    """Init a git repo, write managed metadata, set origin, commit, and push.

    Idempotent: safe to re-run.  On re-init (``.git`` already present) it
    fetches and rebases on the remote before pushing, so commits pushed by
    another device are preserved.

    *branch* sets the initial branch name for new repos and migrates
    existing repos from a legacy branch name (e.g. ``master``) to the
    configured name.
    """
    home.mkdir(parents=True, exist_ok=True)
    already_repo = (home / ".git").is_dir()
    if not already_repo:
        _git(["init", "-q"], home)
        # Set the initial branch explicitly — works on all Git versions
        # (older Git without ``init -b`` would otherwise default to ``master``).
        _git(["symbolic-ref", "HEAD", f"refs/heads/{branch}"], home)
    _prepare_repo_metadata(home)
    remotes = _git(["remote"], home).split()
    if "origin" in remotes:
        _git(["remote", "set-url", "origin", remote], home)
    else:
        _git(["remote", "add", "origin", remote], home)
    _commit(home, "backup init")
    if already_repo:
        _ensure_branch(home, branch)
        _reconcile_remote(home)
    _git(["push", "-u", "origin", branch], home)


def backup(
    home: Path,
    *,
    force: bool = False,
    branch: str = "main",
    durable_resolver=None,
) -> bool:
    """Commit local changes, fetch/rebase the remote branch, and push.

    Returns ``True`` if the **remote was advanced** (a new commit was pushed
    or a previously-rejected push finally succeeded).  Returns ``False`` when
    the remote was already up-to-date.

    Push is always attempted, even without a new commit, so a previously-
    rejected push (remote divergence or a network glitch) is retried
    automatically.

    When *force* is True, snapshot conflicts on graph/wiki are auto-resolved
    (remote version wins) instead of raising an error. Durable conflicts on
    raw/schema/pending always require manual merge.

    *branch* migrates an existing repo from a legacy branch name (e.g.
    ``master``) to the configured name on first run after upgrade.
    """
    if not (home / ".git").is_dir():
        raise BackupError(
            f"not a backup repo at {home} — run `lorekeep backup --init <remote>` first"
        )
    _prepare_repo_metadata(home)
    _commit(home, "backup")
    _ensure_branch(home, branch)
    before = _remote_sha(home)
    _reconcile_remote(
        home, auto_fix_snapshots=force, durable_resolver=durable_resolver,
    )
    _git(["push"], home)
    after = _remote_sha(home)
    return after != before
