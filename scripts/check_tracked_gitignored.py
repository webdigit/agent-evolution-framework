"""Fail if a git-tracked path matches a .gitignore rule.

``git add -f`` can sneak ignored process files into HEAD. This check
makes that visible on every CI run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def tracked_ignored_paths(repository: Path) -> list[str]:
    repository = repository.resolve()
    git = ["git", "-c", f"safe.directory={repository.as_posix()}"]
    result = subprocess.run(
        [*git, "ls-files", "-ci", "-z", "--exclude-standard"],
        cwd=repository,
        capture_output=True,
        check=True,
    )
    names = result.stdout.split(b"\0")
    return [name.decode("utf-8") for name in names if name]


def main() -> int:
    repository = Path.cwd()
    paths = tracked_ignored_paths(repository)
    if not paths:
        return 0
    sys.stderr.write(
        "tracked paths match a .gitignore rule "
        "(remove them with git rm --cached, do not git add -f):\n"
    )
    for path in paths:
        sys.stderr.write(f"  {path}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
