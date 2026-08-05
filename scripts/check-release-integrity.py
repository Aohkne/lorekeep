#!/usr/bin/env python3
"""Fail when committed release versions or built artifacts disagree."""

from __future__ import annotations

import argparse
import ast
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import zipfile


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
CHANGELOG_VERSION_RE = re.compile(r"^## \[?([^\s\]]+)", re.MULTILINE)


class IntegrityError(Exception):
    """A user-actionable collection of release integrity failures."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError([f"cannot read {path}: {exc}"]) from exc


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise IntegrityError([f"cannot read {path}: {exc}"]) from exc


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise IntegrityError([f"{label} must be a non-empty string"])
    return value


def _manifest_version(root: Path) -> str:
    data = _load_json(root / ".release-please-manifest.json")
    if not isinstance(data, dict):
        raise IntegrityError([".release-please-manifest.json must contain an object"])
    return _require_string(data.get("."), ".release-please-manifest.json['.']")


def _project_metadata(root: Path) -> tuple[str, str]:
    data = _load_toml(root / "pyproject.toml")
    project = data.get("project")
    if not isinstance(project, dict):
        raise IntegrityError(["pyproject.toml is missing [project]"])
    name = _require_string(project.get("name"), "pyproject.toml project.name")
    version = _require_string(project.get("version"), "pyproject.toml project.version")
    return name, version


def _module_version(root: Path) -> str:
    path = root / "src" / "lorekeep" / "__init__.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise IntegrityError([f"cannot read {path}: {exc}"]) from exc

    values: list[str] = []
    for node in tree.body:
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__version__"
        ):
            value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.append(value.value)

    if len(values) != 1:
        raise IntegrityError(
            [f"{path} must define exactly one literal __version__; found {len(values)}"]
        )
    return values[0]


def _lock_version(root: Path, project_name: str) -> str:
    path = root / "uv.lock"
    data = _load_toml(path)
    packages = data.get("package", [])
    if not isinstance(packages, list):
        raise IntegrityError(["uv.lock is missing [[package]] entries"])

    local_packages: list[dict[str, object]] = []
    for package in packages:
        if not isinstance(package, dict) or package.get("name") != project_name:
            continue
        source = package.get("source")
        if isinstance(source, dict) and source.get("editable") == ".":
            local_packages.append(package)

    if len(local_packages) != 1:
        raise IntegrityError(
            [
                "uv.lock must contain exactly one editable "
                f"{project_name!r} package; found {len(local_packages)}"
            ]
        )
    return _require_string(local_packages[0].get("version"), "uv.lock local version")


def _changelog_version(root: Path) -> str:
    path = root / "CHANGELOG.md"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntegrityError([f"cannot read {path}: {exc}"]) from exc
    match = CHANGELOG_VERSION_RE.search(content)
    if match is None:
        raise IntegrityError(["CHANGELOG.md has no version heading"])
    return match.group(1)


def _metadata_headers(content: bytes, label: str) -> tuple[str, str]:
    message = BytesParser(policy=policy.default).parsebytes(content)
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise IntegrityError([f"{label} metadata must contain Name and Version"])
    return str(name), str(version)


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(members) != 1:
                raise IntegrityError(
                    [f"{path} must contain exactly one .dist-info/METADATA file"]
                )
            return _metadata_headers(archive.read(members[0]), str(path))
    except (OSError, zipfile.BadZipFile) as exc:
        raise IntegrityError([f"cannot read wheel {path}: {exc}"]) from exc


def _sdist_metadata(path: Path) -> tuple[str, str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.isfile() and member.name.endswith("/PKG-INFO")
            ]
            if len(members) != 1:
                raise IntegrityError([f"{path} must contain exactly one PKG-INFO file"])
            extracted = archive.extractfile(members[0])
            if extracted is None:
                raise IntegrityError([f"cannot extract metadata from {path}"])
            return _metadata_headers(extracted.read(), str(path))
    except (OSError, tarfile.TarError) as exc:
        raise IntegrityError([f"cannot read sdist {path}: {exc}"]) from exc


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _artifact_errors(dist: Path, project_name: str, version: str) -> list[str]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    errors: list[str] = []
    if len(wheels) != 1:
        errors.append(f"{dist} must contain exactly one wheel; found {len(wheels)}")
    if len(sdists) != 1:
        errors.append(f"{dist} must contain exactly one sdist; found {len(sdists)}")
    if errors:
        return errors

    for path, reader in ((wheels[0], _wheel_metadata), (sdists[0], _sdist_metadata)):
        try:
            artifact_name, artifact_version = reader(path)
        except IntegrityError as exc:
            errors.extend(exc.errors)
            continue
        if _normalized_name(artifact_name) != _normalized_name(project_name):
            errors.append(
                f"{path}: expected project {project_name!r}, found {artifact_name!r}"
            )
        if artifact_version != version:
            errors.append(
                f"{path}: expected version {version!r}, found {artifact_version!r}"
            )
    return errors


def check_release(root: Path, *, tag: str | None = None, dist: Path | None = None) -> str:
    """Validate source and optional artifacts, returning the canonical version."""

    project_name, expected = _project_metadata(root)
    versions = {
        ".release-please-manifest.json": _manifest_version(root),
        "pyproject.toml": expected,
        "src/lorekeep/__init__.py": _module_version(root),
        "uv.lock": _lock_version(root, project_name),
        "CHANGELOG.md": _changelog_version(root),
    }

    errors = [
        f"{path}: expected version {expected!r}, found {version!r}"
        for path, version in versions.items()
        if version != expected
    ]
    if SEMVER_RE.fullmatch(expected) is None:
        errors.append(f"pyproject.toml version is not SemVer: {expected!r}")
    if tag is not None and tag != f"v{expected}":
        errors.append(f"release tag: expected {f'v{expected}'!r}, found {tag!r}")
    if dist is not None:
        errors.extend(_artifact_errors(dist, project_name, expected))

    if errors:
        raise IntegrityError(errors)
    return expected


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the script's parent repository)",
    )
    parser.add_argument("--tag", help="expected release tag, including the v prefix")
    parser.add_argument("--dist", type=Path, help="directory containing one wheel and sdist")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    dist = args.dist.resolve() if args.dist is not None else None
    try:
        version = check_release(root, tag=args.tag, dist=dist)
    except IntegrityError as exc:
        print("release integrity check failed:", file=sys.stderr)
        for error in exc.errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"release integrity OK: lorekeep {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
