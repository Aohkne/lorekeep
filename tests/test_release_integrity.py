from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import subprocess
import sys
import tarfile
import zipfile

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check-release-integrity.py"
VERSION = "1.2.3"


def _write_repo(
    root: Path,
    *,
    manifest: str = VERSION,
    project: str = VERSION,
    module: str = VERSION,
    lock: str = VERSION,
    changelog: str = VERSION,
    lock_entries: int = 1,
) -> None:
    (root / "src" / "lorekeep").mkdir(parents=True)
    (root / ".release-please-manifest.json").write_text(
        json.dumps({".": manifest}), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "lorekeep"\nversion = "{project}"\n',
        encoding="utf-8",
    )
    (root / "src" / "lorekeep" / "__init__.py").write_text(
        f'__version__ = "{module}"\n', encoding="utf-8"
    )
    packages = "".join(
        "\n[[package]]\n"
        'name = "lorekeep"\n'
        f'version = "{lock}"\n'
        'source = { editable = "." }\n'
        for _ in range(lock_entries)
    )
    (root / "uv.lock").write_text(f"version = 1\n{packages}", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{changelog}] (2026-01-01)\n",
        encoding="utf-8",
    )


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_artifacts(dist: Path, *, wheel_version: str, sdist_version: str) -> None:
    dist.mkdir()
    wheel = dist / f"lorekeep-{wheel_version}-py3-none-any.whl"
    wheel_metadata = (
        "Metadata-Version: 2.4\n"
        "Name: lorekeep\n"
        f"Version: {wheel_version}\n\n"
    ).encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"lorekeep-{wheel_version}.dist-info/METADATA", wheel_metadata
        )

    sdist = dist / f"lorekeep-{sdist_version}.tar.gz"
    sdist_metadata = (
        "Metadata-Version: 2.4\n"
        "Name: lorekeep\n"
        f"Version: {sdist_version}\n\n"
    ).encode()
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo(f"lorekeep-{sdist_version}/PKG-INFO")
        info.size = len(sdist_metadata)
        archive.addfile(info, BytesIO(sdist_metadata))


def test_matching_release_metadata_passes(tmp_path: Path) -> None:
    _write_repo(tmp_path)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert "release integrity OK: lorekeep 1.2.3" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("field", "expected_file"),
    [
        ("manifest", ".release-please-manifest.json"),
        ("module", "src/lorekeep/__init__.py"),
        ("lock", "uv.lock"),
        ("changelog", "CHANGELOG.md"),
    ],
)
def test_mismatched_source_version_fails(
    tmp_path: Path, field: str, expected_file: str
) -> None:
    kwargs = {field: "9.9.9"}
    _write_repo(tmp_path, **kwargs)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert expected_file in result.stderr
    assert "expected version '1.2.3', found '9.9.9'" in result.stderr


def test_non_semver_project_version_fails(tmp_path: Path) -> None:
    _write_repo(
        tmp_path,
        manifest="next",
        project="next",
        module="next",
        lock="next",
        changelog="next",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "pyproject.toml version is not SemVer: 'next'" in result.stderr


@pytest.mark.parametrize("lock_entries", [0, 2])
def test_requires_exactly_one_editable_project_package(
    tmp_path: Path, lock_entries: int
) -> None:
    _write_repo(tmp_path, lock_entries=lock_entries)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert f"found {lock_entries}" in result.stderr
    assert "exactly one editable 'lorekeep' package" in result.stderr


def test_release_tag_must_match_source_version(tmp_path: Path) -> None:
    _write_repo(tmp_path)

    result = _run(tmp_path, "--tag", "v1.2.4")

    assert result.returncode == 1
    assert "release tag: expected 'v1.2.3', found 'v1.2.4'" in result.stderr


def test_distribution_metadata_passes(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    dist = tmp_path / "dist"
    _write_artifacts(dist, wheel_version=VERSION, sdist_version=VERSION)

    result = _run(tmp_path, "--tag", "v1.2.3", "--dist", str(dist))

    assert result.returncode == 0


def test_distribution_version_mismatch_fails(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    dist = tmp_path / "dist"
    _write_artifacts(dist, wheel_version="1.2.4", sdist_version=VERSION)

    result = _run(tmp_path, "--dist", str(dist))

    assert result.returncode == 1
    assert "expected version '1.2.3', found '1.2.4'" in result.stderr


def test_distribution_requires_one_wheel_and_sdist(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()

    result = _run(tmp_path, "--dist", str(dist))

    assert result.returncode == 1
    assert "exactly one wheel; found 0" in result.stderr
    assert "exactly one sdist; found 0" in result.stderr


def test_repository_release_metadata_is_consistent() -> None:
    result = _run(REPO_ROOT)

    assert result.returncode == 0, result.stderr
