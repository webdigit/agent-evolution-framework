"""Resolve the tree under test on Windows and POSIX.

Every banc script imports ROOT and AEF from here. Windows uses
``.venv\\Scripts``; POSIX uses ``.venv/bin``. The banc must not depend on
either layout.

Exit codes for banc scripts:

* 0 — every property this script checks holds
* 77 — IGNORE: the host cannot run this check (never a success)
* any other non-zero — at least one property does not hold
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ON_WINDOWS = os.name == "nt"
IGNORE_EXIT = 77
ON_WINDOWS = ON_WINDOWS
IGNORE_EXIT = IGNORE_EXIT


def _resolve_root() -> Path:
    raw = os.environ.get("AEF_BUILD")
    if not raw:
        sys.stderr.write(
            "AEF_BUILD is not set.\n"
            "  POSIX      : AEF_BUILD=/tmp/audit-<sha> python 01-....py\n"
            "  PowerShell : $env:AEF_BUILD='C:\\Temp\\audit-<sha>'; python 01-....py\n"
            "  current tree: python 00-setup.py --current && "
            "set AEF_BUILD to the repository root.\n"
        )
        raise SystemExit(2)
    return Path(raw).resolve()


ROOT = _resolve_root()
_BIN = ROOT / (".venv/Scripts" if ON_WINDOWS else ".venv/bin")
_EXE = ".exe" if ON_WINDOWS else ""

AEF = str(_BIN / ("aef" + _EXE))
PY = str(_BIN / ("python" + _EXE))

if not Path(AEF).exists():
    sys.stderr.write(
        "Executable not found: %s\n"
        "Run 00-setup.py first (SHA worktree or --current). It creates the "
        "venv and proves this tree is the one that gets imported.\n" % AEF
    )
    raise SystemExit(2)


def verifier_arbre_importe() -> None:
    """Refuse to measure if a different tree is imported.

    Without this check, a patch-based measurement can hit a copy while pytest
    imports another tree: the verdict is then worthless.
    """
    expected = str((ROOT / "src" / "aef").resolve())
    out = subprocess.run(
        [PY, "-c", "import aef; print(aef.__path__[0])"],
        capture_output=True,
        text=True,
        cwd=str(Path.home()),
    ).stdout.strip()
    if Path(out).resolve() != Path(expected).resolve():
        sys.stderr.write(
            "STOP: imported tree is %s, expected %s.\n"
            "Any patch-based measurement would be false.\n" % (out, expected)
        )
        raise SystemExit(1)


def exiger_posix(reason: str) -> None:
    """Leave without looking like a success when the host cannot run the check."""
    if ON_WINDOWS:
        print("  IGNORE on Windows — %s" % reason)
        print("  (run on Linux CI; do not conclude from this host)")
        raise SystemExit(IGNORE_EXIT)


def finir(failures: int) -> None:
    """Exit 0 iff no property failed."""
    if failures:
        print("*** %d property failure(s) ***" % failures)
        raise SystemExit(1)
    raise SystemExit(0)
