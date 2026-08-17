from pathlib import Path
from copy import deepcopy

import pytest

from aef.filesystem import apply_workspace, load_workspace
from aef.operations import init_project


ROLE = "decision.role.primary.v1"


def test_real_filesystem_init_twice_is_noop(tmp_path: Path):
    required = ["decision.role.primary.v1"]
    answers = {
        "decision.role.primary.v1": "generalist-agent"
    }

    before = load_workspace(tmp_path)

    status1, desired1, _ = init_project(
        before,
        instance_id="test-agent",
        answers=answers,
        required_decisions=required,
        created_at="2026-08-13T18:00:00+02:00",
    )

    assert status1 == "CHANGE"

    diff1 = apply_workspace(
        tmp_path,
        before,
        desired1,
    )

    assert ".agent/manifest.json" in diff1["created"]

    # Reload from the real filesystem.
    reloaded = load_workspace(tmp_path)

    status2, desired2, _ = init_project(
        reloaded,
        instance_id="test-agent",
        answers=answers,
        required_decisions=required,
        created_at="2026-08-13T18:00:00+02:00",
    )

    diff2 = apply_workspace(
        tmp_path,
        reloaded,
        desired2,
    )

    assert status2 == "NO_CHANGE"
    assert diff2 == {
        "created": [],
        "modified": [],
        "removed": [],
    }


def _workspace_bytes(root: Path):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("seed_files,init_kwargs,expected_reason", [
    (
        {},
        {"instance_id": "agent-1", "profile": "aef-v1"},
        None,
    ),
    (
        {
            ".agent/manifest.json": {
                "framework": "other-framework",
                "instance_id": "agent-1",
            }
        },
        {"instance_id": "agent-1"},
        "framework_mismatch",
    ),
    (
        {
            ".agent/manifest.json": {
                "framework": "aef",
                "instance_id": "existing-agent",
            }
        },
        {"instance_id": "requested-agent"},
        "instance_id_mismatch",
    ),
    (
        {
            ".agent/manifest.json": {
                "framework": "aef",
                "framework_version": "1.0.0",
                "schema_version": "1.0.0",
                "instance_id": "agent-1",
            },
            ".agent/state/decisions.json": {
                "decisions": [{
                    "id": ROLE,
                    "status": "resolved",
                    "value": "support-specialist",
                    "source": "human-confirmed",
                }]
            },
        },
        {
            "instance_id": "agent-1",
            "profile": "aef-v1",
            "answers": {ROLE: "generalist-agent"},
        },
        "decision_conflict",
    ),
])
def test_blocked_init_never_writes_to_real_filesystem(
    tmp_path: Path, seed_files, init_kwargs, expected_reason
):
    if seed_files:
        apply_workspace(tmp_path, load_workspace(tmp_path), {"files": deepcopy(seed_files)})
    current = load_workspace(tmp_path)
    before = _workspace_bytes(tmp_path)

    status, blocked, meta = init_project(current, **init_kwargs)
    diff = apply_workspace(tmp_path, current, blocked)

    assert status == "BLOCKED"
    assert blocked == current
    assert diff == {"created": [], "modified": [], "removed": []}
    assert _workspace_bytes(tmp_path) == before
    if expected_reason is None:
        assert meta["unresolved_decisions"] == [ROLE]
    else:
        assert meta["reason"] == expected_reason


@pytest.mark.parametrize("version_field,actual_version,expected_reason", [
    ("framework_version", "0.9.0", "framework_version_mismatch"),
    ("framework_version", "1.1.0", "framework_version_mismatch"),
    ("framework_version", None, "framework_version_mismatch"),
    ("framework_version", 100, "framework_version_mismatch"),
    ("schema_version", "0.9.0", "schema_version_mismatch"),
    ("schema_version", "1.1.0", "schema_version_mismatch"),
    ("schema_version", None, "schema_version_mismatch"),
    ("schema_version", 100, "schema_version_mismatch"),
])
def test_profile_version_block_never_writes_to_real_filesystem(
    tmp_path: Path, version_field, actual_version, expected_reason
):
    manifest = {
        "framework": "aef",
        "framework_version": "1.0.0",
        "schema_version": "1.0.0",
        "instance_id": "agent-1",
        "created_at": "2026-08-13T18:00:00+02:00",
    }
    if actual_version is None:
        manifest.pop(version_field)
    else:
        manifest[version_field] = actual_version
    seed = {
        ".agent/manifest.json": manifest,
        ".agent/state/decisions.json": {
            "decisions": [{
                "id": ROLE,
                "status": "resolved",
                "value": "generalist-agent",
                "source": "human-confirmed",
            }]
        },
    }
    apply_workspace(tmp_path, load_workspace(tmp_path), {"files": seed})
    current = load_workspace(tmp_path)
    before = _workspace_bytes(tmp_path)

    status, blocked, meta = init_project(current, instance_id="agent-1", profile="aef-v1")
    diff = apply_workspace(tmp_path, current, blocked)

    assert status == "BLOCKED"
    assert meta["reason"] == expected_reason
    assert blocked == current
    assert diff == {"created": [], "modified": [], "removed": []}
    assert _workspace_bytes(tmp_path) == before
