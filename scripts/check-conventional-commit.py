"""Conventional Commit message validator for the commit-msg hook.

Usage: python scripts/check-conventional-commit.py <.git/COMMIT_EDITMSG>
Exits 0 if the first non-comment line of the commit message matches
the Conventional Commits spec (https://www.conventionalcommits.org).
"""

import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(\([^)]*\))?"   # optional scope
    r"(!)?:"           # breaking-change marker (optional)
    r" .+"             # space + description (non-empty)
)

MERGE_RE = re.compile(
    r"^Merge (branch|pull request|remote-tracking|tag) "
)


def first_line(msg_file: Path) -> str:
    lines = msg_file.read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def is_valid(line: str) -> bool:
    return bool(PATTERN.match(line) or MERGE_RE.match(line))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check-conventional-commit.py <.git/COMMIT_EDITMSG>",
              file=sys.stderr)
        sys.exit(1)

    line = first_line(Path(sys.argv[1]))
    if is_valid(line):
        sys.exit(0)

    print("\n⛔  Commit message does NOT follow Conventional Commits.\n", file=sys.stderr)
    print("Format:  type[(scope)][!]: description\n", file=sys.stderr)
    print("Types:   build | chore | ci | docs | feat | fix | perf | refactor | "
          "revert | style | test\n", file=sys.stderr)
    print("Examples:", file=sys.stderr)
    print("  feat: add MCP lazy-reload on facts.jsonl mtime change", file=sys.stderr)
    print("  fix(security): skip symlinks escaping raw_root in ingest", file=sys.stderr)
    print("  chore!: drop Python 3.10 support\n", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
