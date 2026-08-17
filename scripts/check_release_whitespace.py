"""Check whitespace across every tracked file in a release candidate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def check_release_tree(repository: Path) -> subprocess.CompletedProcess[str]:
    repository = repository.resolve()
    git = ["git", "-c", f"safe.directory={repository.as_posix()}"]
    empty_tree = subprocess.run(
        [*git, "mktree"],
        cwd=repository,
        input="",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return subprocess.run(
        [*git, "diff", "--check", empty_tree, "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    result = check_release_tree(Path.cwd())
    if result.stdout:
        sys.stderr.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
