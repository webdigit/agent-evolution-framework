#!/usr/bin/env python3
"""Prepare an auditable tree and PROVE that it is the one that gets imported.

Usage:
  python 00-setup.py <SHA> [<directory>]
  python 00-setup.py --current [<directory>]

``--current`` uses the checkout as-is (CI: the checkout IS the tree).
It still creates the venv, installs the package editable, and runs the
full import-path check from a working directory outside the tree.

Works on Windows and POSIX.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ON_WINDOWS = os.name == "nt"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, **kw)


def venv_python(base: Path) -> Path:
    if ON_WINDOWS:
        return base / ".venv" / "Scripts" / "python.exe"
    return base / ".venv" / "bin" / "python"


def prove_imported_tree(py: Path, base: Path) -> int:
    print("--- MANDATORY CHECK: which tree is imported?")
    # cwd deliberately OUTSIDE the repo: otherwise another tree can win
    completed = subprocess.run(
        [str(py), "-c", "import aef; print(aef.__path__[0])"],
        capture_output=True,
        text=True,
        cwd=str(Path.home()),
    )
    imported = completed.stdout.strip()
    expected = str((base / "src" / "aef").resolve())
    print("    imported : %s" % imported)
    print("    expected : %s" % expected)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        print("    *** STOP: import failed. ***")
        return 1
    if Path(imported).resolve() != Path(expected).resolve():
        print("    *** STOP: a DIFFERENT tree is imported. Any patch measurement would be false. ***")
        return 1
    print("    OK")
    return 0


def install_editable(base: Path) -> Path:
    py = venv_python(base)
    if not py.is_file():
        run([sys.executable, "-m", "venv", str(base / ".venv")])
        py = venv_python(base)
    run([str(py), "-m", "pip", "install", "-q", "-e", str(base) + "[dev]"])
    run([str(py), "-m", "pip", "install", "-q", "setuptools==84.0.0"])
    return py


def setup_worktree(sha: str, base: Path) -> Path:
    repo = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not repo:
        print("This directory is not inside a git repository.")
        raise SystemExit(2)
    if not base.exists():
        run(["git", "-C", repo, "worktree", "add", "--detach", str(base), sha])
    else:
        print("    reusing existing directory %s" % base)
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current",
        action="store_true",
        help="use this checkout (no detached worktree); still prove the import path",
    )
    parser.add_argument(
        "sha_or_dir",
        nargs="?",
        help="SHA to check out, or with --current an optional repository root",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="target directory for a detached worktree",
    )
    args = parser.parse_args()

    if args.current:
        if args.directory:
            parser.error("--current takes at most one optional directory")
        if args.sha_or_dir:
            base = Path(args.sha_or_dir).resolve()
        else:
            top = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not top:
                print("This directory is not inside a git repository.")
                return 2
            base = Path(top)
        print("mode: current tree %s" % base)
    else:
        if not args.sha_or_dir:
            parser.print_help()
            return 2
        sha = args.sha_or_dir
        base = (
            Path(args.directory)
            if args.directory
            else Path(tempfile.gettempdir()) / ("audit-" + sha[:7])
        )
        print("mode: detached worktree %s @ %s" % (base, sha))
        setup_worktree(sha, base)

    py = install_editable(base)
    status = prove_imported_tree(py, base)
    if status != 0:
        return status
    print()
    if ON_WINDOWS:
        print("Then:  $env:AEF_BUILD='%s'; python lance-tout.py" % base)
    else:
        print("Then:  AEF_BUILD=%s python3 lance-tout.py" % base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
