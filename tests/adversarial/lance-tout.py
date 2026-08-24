#!/usr/bin/env python3
"""Run the whole banc against AEF_BUILD and summarise verdicts.

Exit 0 if and only if every runnable script held its properties.
Platform IGNORE (exit 77) is counted as ignored, never as success.
A failing script makes this process exit non-zero and is named below.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bancenv import ROOT, PY, ON_WINDOWS, IGNORE_EXIT  # noqa: E402

HERE = Path(__file__).resolve().parent
SCRIPTS = [
    ("01-concurrence-ingest.py", []),
    ("02-concurrence-declare.py", []),
    ("03-concurrence-record.py", []),
    ("04-plafond-evidences.py", []),
    ("05-dryrun-vs-apply.py", []),
    ("08-audit-scopage.py", []),
    ("09-collision-identifiants.py", []),
    ("06-taux-erreur-fs.py", ["4", "8"]),
    ("07-crash-sigkill.py", []),
    ("11-hygiene-git.py", []),
    ("12-fence-marqueurs.py", []),
    ("13-guidance-integrite.py", []),
    ("14-ecrivain-externe.py", []),
    ("10-epic3-runtime.py", []),
]


def launch(name: str, args: list[str]) -> tuple[int, str]:
    print("=" * 5, name)
    t0 = time.time()
    result = subprocess.run(
        [PY, str(HERE / name), *args],
        capture_output=True,
        text=True,
        cwd=str(HERE),
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output.rstrip()[-4000:])
    elapsed = time.time() - t0
    print("      (%.0f s, code %s)" % (elapsed, result.returncode))
    print()
    return result.returncode, output


def main() -> int:
    print("############ ADVERSARIAL BANC — %s" % ROOT)
    print()
    codes: dict[str, int] = {}
    for name, args in SCRIPTS:
        if not (HERE / name).exists():
            print("=" * 5, name)
            print("  missing script")
            codes[name] = 2
            print()
            continue
        codes[name] = launch(name, args)[0]

    print("=" * 5, "pytest suite + whitespace")
    pytest = subprocess.run(
        [PY, "-m", "pytest", "-q", "--tb=line"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    pytest_lines = (pytest.stdout or pytest.stderr or "").strip().splitlines()
    if pytest.returncode != 0:
        print("  --- pytest output (tail) ---")
        for line in pytest_lines[-40:]:
            print("  " + line)
    else:
        print("  " + (pytest_lines or ["(no output)"])[-1])
    codes["pytest"] = pytest.returncode
    whitespace = subprocess.run(
        [PY, str(ROOT / "scripts" / "check_release_whitespace.py")],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    print(
        "  whitespace exit=%s %s"
        % (whitespace.returncode, (whitespace.stdout or whitespace.stderr).strip()[:200])
    )
    codes["whitespace"] = whitespace.returncode
    print()

    ignored = [name for name, code in codes.items() if code == IGNORE_EXIT]
    failed = [name for name, code in codes.items() if code not in (0, IGNORE_EXIT)]
    print("############ ignored (platform):", ignored or "none")
    print("############ failed scripts:", failed or "none")
    if failed:
        print("############ BANC RED — %d failure(s)" % len(failed))
        return 1
    print("############ BANC GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
