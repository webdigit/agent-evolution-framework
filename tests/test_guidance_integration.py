"""Table-driven guidance segment contracts — no filesystem I/O."""

from __future__ import annotations

from aef.guidance_integration import (
    AGENTS_BYTES,
    CLAUDE_ROOT_BYTES,
    GEMINI_BYTES,
    inspect_door,
    plan_door_integration,
)


def _project():
    return {
        "files": {
            ".agent/manifest.json": {
                "framework": "aef",
                "framework_version": "1.0.0",
                "schema_version": "1.0.0",
                "instance_id": "agent-1",
            },
            ".agent/core/constitution.md": "c",
            ".agent/core/autonomy.md": "a",
            ".agent/core/learning.md": "l",
            ".agent/core/levels.md": "v",
            ".agent/core/scoring.md": "s",
        }
    }


def test_agents_segment_cites_doctrine_and_runtime_without_install_required():
    assert b".agent/core/constitution.md" in AGENTS_BYTES
    assert b"docs/runtime.md" in AGENTS_BYTES
    assert b"aef integrate runtime" in AGENTS_BYTES
    assert b"INSTALL_REQUIRED" not in AGENTS_BYTES
    assert b"Selon l'agent" not in AGENTS_BYTES
    assert inspect_door("agents", AGENTS_BYTES)["state"] == "installed"


def test_agents_1_0_0_remains_recognized_and_upgrades():
    from aef.guidance_integration import AGENTS_BYTES_1_0_0

    assert inspect_door("agents", AGENTS_BYTES_1_0_0)["state"] == "installed"
    assert inspect_door("agents", AGENTS_BYTES_1_0_0)["version"] == "1.0.0"
    status, _, meta = plan_door_integration(
        _project(), AGENTS_BYTES_1_0_0, door="agents",
    )
    assert status == "CHANGE"
    assert meta["integration_version"] == "1.1.0"
    assert inspect_door("agents", meta["desired_bytes"])["state"] == "installed"
    assert inspect_door("agents", meta["desired_bytes"])["version"] == "1.1.0"


def test_doorbells_contain_no_doctrine_rules():
    assert b".agent/core/" not in CLAUDE_ROOT_BYTES
    assert b"@AGENTS.md" in CLAUDE_ROOT_BYTES
    assert b".agent/core/" not in GEMINI_BYTES
    assert b"AGENTS.md" in GEMINI_BYTES
    assert b"permission" in GEMINI_BYTES


def test_replay_installed_is_no_change():
    status, _, meta = plan_door_integration(
        _project(), AGENTS_BYTES, door="agents",
    )
    assert status == "NO_CHANGE"
    assert meta["bridge"]["state"] == "installed"


def test_insert_preserves_user_prefix():
    existing = b"# My notes\r\n"
    status, _, meta = plan_door_integration(
        _project(), existing, door="agents",
    )
    assert status == "CHANGE"
    assert meta["desired_bytes"].startswith(b"# My notes\r\n\n\n")
    assert meta["desired_bytes"].endswith(AGENTS_BYTES)


def test_modified_segment_is_blocked():
    modified = AGENTS_BYTES.replace(b"guidance only", b"authority", 1)
    status, _, meta = plan_door_integration(
        _project(), modified, door="agents",
    )
    assert status == "BLOCKED"
    assert meta["reason"] == "modified_agents_managed_block"
