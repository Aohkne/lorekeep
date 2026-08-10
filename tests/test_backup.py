import subprocess
from pathlib import Path

import pytest

import lorekeep.backup as backup_module
from lorekeep.backup import (
    BACKUP_GITATTRIBUTES,
    BACKUP_GITIGNORE,
    BackupError,
    backup,
    has_remote,
    init_backup,
    sync_backup,
)


def _bare_remote(tmp_path: Path) -> str:
    """A local bare repo usable as a push remote (file:// not required for path remote)."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    return str(bare)


def _tracked(home: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=home, capture_output=True, text=True, check=True
    ).stdout
    return out.split()


def _log(home: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline"], cwd=home, capture_output=True, text=True, check=True
    ).stdout


def _git_cmd(args: list[str], cwd: Path) -> None:
    """Run git with inline identity (same pattern as backup._git)."""
    subprocess.run(
        ["git", "-c", "user.email=test@test.local", "-c", "user.name=test", *args],
        cwd=cwd, check=True,
    )


def test_init_backup_creates_repo_gitignore_and_remote(tmp_path: Path):
    home = tmp_path / "home"
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "a.md").write_text("# a")
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    assert (home / ".git").is_dir()
    assert (home / ".gitignore").read_text() == BACKUP_GITIGNORE
    assert (home / ".gitattributes").read_text() == BACKUP_GITATTRIBUTES
    refs = subprocess.run(
        ["git", "ls-remote", remote], capture_output=True, text=True, check=True
    ).stdout
    assert refs.strip() != ""  # initial commit landed on the remote


def test_backup_commits_and_pushes_new_changes(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "b.md").write_text("# b")
    made = backup(home)
    assert made is True
    assert "backup " in _log(home)


def test_backup_skips_when_nothing_staged(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    pushed = backup(home)
    assert pushed is False


def test_backup_raises_when_not_a_repo(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(BackupError):
        backup(home)


def test_backup_tracks_snapshots_but_not_device_local_state(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "config.yaml").write_text("api_key: sk-leaked\n")
    (home / "graph").mkdir()
    (home / "graph" / "facts.jsonl").write_text("{}")
    (home / "graph" / "manifest.json").write_text("{}")
    (home / "graph" / "fts.sqlite").write_text("local index")
    (home / "wiki").mkdir()
    (home / "wiki" / "index.md").write_text("# snapshot")
    (home / "wiki" / ".obsidian").mkdir()
    (home / "wiki" / ".obsidian" / "workspace.json").write_text("{}")
    (home / "cache.json").write_text("{}")
    (home / "logs").mkdir()
    (home / "logs" / "runtime.log").write_text("local diagnostics")
    (home / ".daemon.pid").write_text("12345")
    backup(home)
    tracked = _tracked(home)
    assert "config.yaml" not in tracked
    assert "graph/facts.jsonl" in tracked
    assert "graph/manifest.json" in tracked
    assert "wiki/index.md" in tracked
    assert "cache.json" not in tracked
    assert "graph/fts.sqlite" not in tracked
    assert "wiki/.obsidian/workspace.json" not in tracked
    assert "logs/runtime.log" not in tracked
    assert ".daemon.pid" not in tracked


def test_backup_migrates_legacy_ignores_and_preserves_custom_rules(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / ".gitignore").write_text(
        "config.yaml\ngraph/facts.jsonl\ngraph/manifest.json\nwiki/\n"
        "cache.json\nfts.sqlite\n*.lock\ncustom-local/\n"
    )
    (home / "graph").mkdir()
    (home / "graph" / "facts.jsonl").write_text("{}\n")
    (home / "graph" / "manifest.json").write_text("{}\n")
    (home / "wiki").mkdir()
    (home / "wiki" / "index.md").write_text("# restored without compile\n")

    assert backup(home) is True

    ignore = (home / ".gitignore").read_text()
    assert "graph/facts.jsonl" not in ignore
    assert "graph/manifest.json" not in ignore
    assert "\nwiki/\n" not in f"\n{ignore}"
    assert "custom-local/" in ignore
    assert "logs/" in ignore
    assert "graph/facts.jsonl" in _tracked(home)
    assert "wiki/index.md" in _tracked(home)


def test_backup_tracks_journals_for_cross_device_replay(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "pending" / "public").mkdir(parents=True)
    (home / "pending" / "public" / "journal.jsonl").write_text(
        '{"id":"x","kind":"node","ns":"public","label":"leaked","type":"Concept"}\n'
    )
    backup(home)
    tracked = _tracked(home)
    assert "pending/public/journal.jsonl" in tracked


def test_backup_retries_previously_rejected_push(tmp_path: Path):
    """Even with nothing new staged, backup() must still push — retrying a
    commit that landed locally but never reached the remote.  The remote SHA
    changes, so ``backup()`` returns ``True``."""
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    # Make a local commit that the remote does not yet have, with nothing new
    # staged afterward (so _commit() returns False internally).
    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "pre.md").write_text("# pre")
    _git_cmd(["add", "-A"], home)
    _git_cmd(["commit", "-q", "-m", "pre-made local"], home)
    pushed = backup(home)
    assert pushed is True  # remote advanced — pre-made commit was pushed
    # The remote's HEAD must point at our local HEAD.
    remote_refs = subprocess.run(
        ["git", "ls-remote", remote],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    remote_head = remote_refs.splitlines()[0].split()[0]
    local_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=home,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert remote_head == local_head


def test_backup_rebases_disjoint_device_changes_before_push(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", remote, str(other)], check=True)
    remote_file = other / "raw" / "remote" / "remote.md"
    remote_file.parent.mkdir(parents=True)
    remote_file.write_text("# remote")
    _git_cmd(["add", "-A"], other)
    _git_cmd(["commit", "-q", "-m", "remote raw"], other)
    subprocess.run(["git", "push", "-q"], cwd=other, check=True)

    local_file = home / "raw" / "local" / "local.md"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("# local")

    assert backup(home) is True
    assert (home / "raw" / "remote" / "remote.md").exists()
    assert local_file.exists()


def test_backup_generated_snapshot_conflict_aborts_without_line_merge(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    (home / "graph").mkdir()
    (home / "wiki").mkdir()
    (home / "graph" / "facts.jsonl").write_text('{"id":"base"}\n')
    (home / "graph" / "manifest.json").write_text('{"run_id":"base"}\n')
    (home / "wiki" / "index.md").write_text("# base\n")
    assert backup(home) is True

    other = tmp_path / "other-snapshot"
    subprocess.run(["git", "clone", "-q", remote, str(other)], check=True)
    (other / "graph" / "facts.jsonl").write_text('{"id":"remote"}\n')
    (other / "wiki" / "index.md").write_text("# remote\n")
    _git_cmd(["add", "-A"], other)
    _git_cmd(["commit", "-q", "-m", "remote snapshot"], other)
    subprocess.run(["git", "push", "-q"], cwd=other, check=True)

    (home / "graph" / "facts.jsonl").write_text('{"id":"local"}\n')
    (home / "wiki" / "index.md").write_text("# local\n")
    with pytest.raises(BackupError, match="Never line-merge graph/ or wiki/"):
        backup(home)

    assert not (home / ".git" / "rebase-merge").exists()
    assert not (home / ".git" / "rebase-apply").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=home,
        capture_output=True, text=True, check=True,
    ).stdout
    assert status == ""
    assert (home / "graph" / "facts.jsonl").read_text() == '{"id":"local"}\n'
    assert (home / "wiki" / "index.md").read_text() == "# local\n"


def test_init_backup_idempotent_on_diverged_remote(tmp_path: Path):
    """Re-init with a remote that has commits the local lacks should not fail."""
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)

    # Simulate another device pushing to the remote
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", remote, str(other)], check=True)
    (other / "raw" / "ns2").mkdir(parents=True)
    (other / "raw" / "ns2" / "remote.md").write_text("# from remote")
    _git_cmd(["add", "-A"], other)
    _git_cmd(["commit", "-q", "-m", "remote-side change"], other)
    subprocess.run(["git", "push", "-q"], cwd=other, check=True)

    # Now re-init on the original device — should rebase + push without error
    init_backup(home, remote)
    # The remote-side file should be present locally after rebase
    assert (home / "raw" / "ns2" / "remote.md").exists()


# ── has_remote + sync_backup tests ────────────────────────────────────────


def test_has_remote_false_when_no_repo(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    assert has_remote(home) is False


def test_has_remote_false_when_no_origin(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=home, check=True)
    assert has_remote(home) is False


def test_has_remote_true_after_init(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    assert has_remote(home) is True


def test_sync_backup_no_repo_returns_false(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    assert sync_backup(home) is False


def test_sync_backup_commits_and_pushes(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)

    (home / "raw" / "ns").mkdir(parents=True)
    (home / "raw" / "ns" / "new.md").write_text("# new")
    pushed = sync_backup(home)
    assert pushed is True


def test_sync_backup_nothing_to_push(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    pushed = sync_backup(home)
    assert pushed is False


def test_sync_backup_pulls_from_other_machine(tmp_path: Path):
    """Sync should pull changes pushed by another device before pushing."""
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)

    # Simulate another machine pushing a new file
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", remote, str(other)], check=True)
    (other / "raw" / "shared").mkdir(parents=True)
    (other / "raw" / "shared" / "synced.md").write_text("# from other device")
    _git_cmd(["add", "-A"], other)
    _git_cmd(["commit", "-q", "-m", "other device"], other)
    subprocess.run(["git", "push", "-q"], cwd=other, check=True)

    # Now sync on the original device — should pull the file
    (home / "raw" / "local").mkdir(parents=True)
    (home / "raw" / "local" / "local.md").write_text("# local change")
    sync_backup(home)

    assert (home / "raw" / "shared" / "synced.md").exists()
    assert (home / "raw" / "local" / "local.md").exists()


def test_sync_backup_commits_tracked_changes_before_rebase(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    tracked = home / "raw" / "shared" / "tracked.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("# v1")
    assert sync_backup(home) is True

    other = tmp_path / "other-tracked"
    subprocess.run(["git", "clone", "-q", remote, str(other)], check=True)
    remote_only = other / "raw" / "remote" / "remote.md"
    remote_only.parent.mkdir(parents=True)
    remote_only.write_text("# remote")
    _git_cmd(["add", "-A"], other)
    _git_cmd(["commit", "-q", "-m", "remote change"], other)
    subprocess.run(["git", "push", "-q"], cwd=other, check=True)

    tracked.write_text("# v2 local")
    assert sync_backup(home) is True
    assert tracked.read_text() == "# v2 local"
    assert (home / "raw" / "remote" / "remote.md").exists()


def test_sync_backup_handles_network_error(tmp_path: Path):
    home = tmp_path / "home"
    remote = _bare_remote(tmp_path)
    init_backup(home, remote)
    # Break the remote URL
    subprocess.run(["git", "remote", "set-url", "origin", "/nonexistent/path"],
                   cwd=home, check=True)
    # Should not raise — returns False silently
    result = sync_backup(home)
    assert result is False


def test_reconcile_remote_detached_head_skips_rebase(tmp_path: Path, monkeypatch):
    calls = []

    def fake_git(args, _cwd):
        calls.append(args)
        return "HEAD" if args[:2] == ["rev-parse", "--abbrev-ref"] else ""

    monkeypatch.setattr(backup_module, "_git", fake_git)
    backup_module._reconcile_remote(tmp_path)
    assert not any(args[0] == "rebase" for args in calls)


def test_reconcile_remote_without_matching_remote_branch_skips(tmp_path: Path, monkeypatch):
    def fake_git(args, _cwd):
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return "main"
        if args[:2] == ["rev-parse", "--verify"]:
            raise BackupError("missing ref")
        return ""

    monkeypatch.setattr(backup_module, "_git", fake_git)
    backup_module._reconcile_remote(tmp_path)


def test_reconcile_remote_unknown_conflict_still_aborts(tmp_path: Path, monkeypatch):
    def fake_git(args, _cwd):
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return "main"
        if args[0] == "rebase" and args[1] != "--abort":
            raise BackupError("conflict")
        if args[:3] == ["diff", "--name-only", "--diff-filter=U"]:
            raise BackupError("cannot inspect")
        return ""

    monkeypatch.setattr(backup_module, "_git", fake_git)
    with pytest.raises(BackupError, match="Conflicts: unknown paths"):
        backup_module._reconcile_remote(tmp_path)


def test_reconcile_remote_abort_failure_is_actionable(tmp_path: Path, monkeypatch):
    def fake_git(args, _cwd):
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return "main"
        if args[0] == "rebase":
            if args[1] == "--abort":
                raise BackupError("abort failed")
            raise BackupError("conflict")
        if args[:3] == ["diff", "--name-only", "--diff-filter=U"]:
            return "graph/facts.jsonl"
        return ""

    monkeypatch.setattr(backup_module, "_git", fake_git)
    with pytest.raises(BackupError, match="could not be aborted"):
        backup_module._reconcile_remote(tmp_path)
