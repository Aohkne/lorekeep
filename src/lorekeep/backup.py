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
        "wiki/.trash", "wiki/.DS_Store", "logs", ".daemon.pid",
    ], home)


def _remote_sha(home: Path) -> str | None:
    """SHA of the remote branch tracking the current local branch, or None."""
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], home)
    if not branch or branch == "HEAD":
        return None
    out = _git(["ls-remote", "origin", branch], home)
    return out.split()[0] if out.strip() else None


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


def _resolve_snapshot_conflict_inplace(home: Path) -> list[str]:
    """Resolve snapshot conflicts during an active rebase (in-place).

    Must be called while a rebase is in progress with unmerged paths.
    For each conflicted graph/wiki path: accepts the remote version
    (``checkout --ours`` during rebase, which is the upstream/remote),
    stages it. Returns the list of durable conflicts that could not be
    auto-resolved.
    """
    conflicts = _git(["diff", "--name-only", "--diff-filter=U"], home)
    snapshot, durable = _classify_conflicts(conflicts)
    for path in snapshot:
        # During rebase: --ours = upstream (remote), --theirs = local commit
        _git(["checkout", "--ours", "--", path], home)
        _git(["add", "--", path], home)
    return durable


def _reconcile_remote(home: Path, *, auto_fix_snapshots: bool = False) -> None:
    """Fetch + rebase, aborting cleanly rather than leaving a broken repo.

    The generated graph/wiki paths use ``-merge`` attributes. If two devices
    published different snapshots, Git stops instead of line-merging them.

    When *auto_fix_snapshots* is True (daemon mode), snapshot-only conflicts
    are auto-resolved by accepting the remote version. The daemon recompiles
    locally afterwards. Durable conflicts always require user intervention.
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
            durable = _resolve_snapshot_conflict_inplace(home) \
                if auto_fix_snapshots else None
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


def sync_backup(home: Path, *, auto_fix: bool = True) -> bool:
    """Sync backup: pull --rebase from remote, then commit + push.

    Used by daemon after compile/resolve/heal. Pulls changes from other
    machines first (fetch + rebase), then pushes local changes.

    When *auto_fix* is True (default), snapshot conflicts on graph/wiki
    are auto-resolved by accepting the remote version. The daemon will
    recompile locally to align with merged durable inputs.

    Silently returns False if:
    - No backup repo or remote configured
    - Network error or durable conflict (user resolves with ``lorekeep backup``)
    """
    if not has_remote(home):
        return False
    try:
        _prepare_repo_metadata(home)
        before = _remote_sha(home)
        # Commit first so tracked journal/raw changes do not block rebase.
        _commit(home, "backup")
        _reconcile_remote(home, auto_fix_snapshots=auto_fix)
        _git(["push"], home)
        after = _remote_sha(home)
        return after != before
    except BackupError:
        return False


def init_backup(home: Path, remote: str) -> None:
    """Init a git repo, write managed metadata, set origin, commit, and push.

    Idempotent: safe to re-run.  On re-init (``.git`` already present) it
    fetches and rebases on the remote before pushing, so commits pushed by
    another device are preserved.
    """
    home.mkdir(parents=True, exist_ok=True)
    already_repo = (home / ".git").is_dir()
    if not already_repo:
        _git(["init", "-q"], home)
    _prepare_repo_metadata(home)
    remotes = _git(["remote"], home).split()
    if "origin" in remotes:
        _git(["remote", "set-url", "origin", remote], home)
    else:
        _git(["remote", "add", "origin", remote], home)
    _commit(home, "backup init")
    if already_repo:
        _reconcile_remote(home)
    _git(["push", "-u", "origin", "HEAD"], home)


def backup(home: Path, *, force: bool = False) -> bool:
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
    """
    if not (home / ".git").is_dir():
        raise BackupError(
            f"not a backup repo at {home} — run `lorekeep backup --init <remote>` first"
        )
    _prepare_repo_metadata(home)
    before = _remote_sha(home)
    _commit(home, "backup")
    _reconcile_remote(home, auto_fix_snapshots=force)
    _git(["push"], home)
    after = _remote_sha(home)
    return after != before
