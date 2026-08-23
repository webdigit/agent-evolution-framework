#!/usr/bin/env python3
"""POSIX wrapper: Epic 3 runtime (zip bomb, binary exec, network). IGNORE on Windows."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bancenv import verifier_arbre_importe, exiger_posix  # noqa: E402

verifier_arbre_importe()
exiger_posix("strace, /usr/bin/time, zip-bomb RSS — Linux only")

script = Path(__file__).resolve().parent / "10-epic3-runtime.sh"
if not script.is_file():
    print("missing %s" % script)
    raise SystemExit(2)
result = subprocess.run(["bash", str(script)])
raise SystemExit(result.returncode)
