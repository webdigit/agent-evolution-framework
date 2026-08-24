#!/usr/bin/env python3
"""Decomposition exacte du delta de tests entre deux worktrees.

Un renommage compte UNE fois de chaque cote — c'est le point qui a ete
compte d'un seul cote trois fois de suite.

Usage :  python decompte.py <worktree-avant> <worktree-apres>
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ON_WINDOWS = os.name == "nt"


def collecte(worktree: str) -> set[str]:
    root = Path(worktree).resolve()
    py = root / (".venv/Scripts" if ON_WINDOWS else ".venv/bin") / ("python.exe" if ON_WINDOWS else "python")
    out = subprocess.run([str(py), "-m", "pytest", "--collect-only", "-q"],
                         capture_output=True, text=True, cwd=str(root)).stdout
    return {l.strip() for l in out.splitlines() if "::" in l}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    avant, apres = collecte(sys.argv[1]), collecte(sys.argv[2])
    ajouts, retraits = sorted(apres - avant), sorted(avant - apres)
    print("AJOUTS (%d) :" % len(ajouts))
    for t in ajouts:
        print("  " + t.split("::")[-1])
    print("RETRAITS (%d) :" % len(retraits))
    for t in retraits:
        print("  " + t.split("::")[-1])
    print("NET : +%d / -%d = %+d" % (len(ajouts), len(retraits), len(ajouts) - len(retraits)))
    print("(%d avant, %d apres)" % (len(avant), len(apres)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
