from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aef.filesystem import apply_workspace, load_workspace
from aef.operations import init_project


def run(workspace: Path) -> int:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    required = ["decision.role.primary.v1"]
    answers = {
        "decision.role.primary.v1": "generalist-agent",
    }

    # First INIT
    before = load_workspace(workspace)
    status1, desired1, meta1 = init_project(
        before,
        instance_id="pilot-neutral-agent-001",
        answers=answers,
        required_decisions=required,
        created_at="2026-08-13T18:00:00+02:00",
    )

    if status1 not in {"CHANGE", "NO_CHANGE"}:
        print(json.dumps({
            "phase": "first_init",
            "status": status1,
            "meta": meta1,
        }, indent=2))
        return 1

    diff1 = apply_workspace(workspace, before, desired1)

    # Reload from the real filesystem.
    after_first_write = load_workspace(workspace)

    # Second INIT must be a no-op.
    status2, desired2, meta2 = init_project(
        after_first_write,
        instance_id="pilot-neutral-agent-001",
        answers=answers,
        required_decisions=required,
        created_at="2026-08-13T18:00:00+02:00",
    )

    diff2 = apply_workspace(workspace, after_first_write, desired2)

    result = {
        "workspace": str(workspace),
        "first_init": {
            "status": status1,
            "diff": diff1,
            "meta": meta1,
        },
        "second_init": {
            "status": status2,
            "diff": diff2,
            "meta": meta2,
        },
        "pass": (
            status2 == "NO_CHANGE"
            and diff2["created"] == []
            and diff2["modified"] == []
            and diff2["removed"] == []
        ),
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if not result["pass"]:
        return 2

    print("\nAEF filesystem pilot: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", help="Path to the neutral pilot workspace")
    args = parser.parse_args()
    return run(Path(args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
