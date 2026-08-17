from copy import deepcopy
from pathlib import Path

import pytest

from aef.claude_integration import (
    CLAUDE_BRIDGE_BYTES, LEGACY_CLAUDE_BRIDGE_BYTES, plan_claude_integration,
)


def project():
    return {
        "files": {
            ".agent/manifest.json": {
                "framework": "aef", "framework_version": "1.0.0",
                "schema_version": "1.0.0",
            },
            **{
                f".agent/core/{name}.md": f"# {name}\n"
                for name in ("constitution", "autonomy", "learning", "levels", "scoring")
            },
        }
    }


def test_bridge_matches_the_independent_contract_fixture():
    expected = (Path(__file__).parent / "expected_claude" / "CLAUDE.md").read_bytes()
    assert CLAUDE_BRIDGE_BYTES == expected


def test_install_replay_and_remove_preserve_all_user_bytes():
    source = project()
    before = b"# User instructions\r\nNo final newline"
    status, out, meta = plan_claude_integration(source, before)
    installed = meta["desired_bytes"]

    assert status == "CHANGE"
    assert installed.startswith(before + b"\n\n")
    assert out == source and out is not source
    assert plan_claude_integration(source, installed)[0] == "NO_CHANGE"
    removed = plan_claude_integration(source, installed, remove=True)
    assert removed[0] == "CHANGE"
    assert removed[2]["desired_bytes"] == before
    assert source == project()


def test_remove_bridge_only_keeps_an_empty_file():
    result = plan_claude_integration(project(), CLAUDE_BRIDGE_BYTES, remove=True)
    assert result[0] == "CHANGE"
    assert result[2]["desired_bytes"] == b""


@pytest.mark.parametrize("content,reason", [
    (CLAUDE_BRIDGE_BYTES.replace(b"guidance", b"permission", 1), "modified_claude_managed_block"),
    (CLAUDE_BRIDGE_BYTES + CLAUDE_BRIDGE_BYTES, "ambiguous_claude_managed_block"),
    (CLAUDE_BRIDGE_BYTES.replace(b'1.0.0', b'9.0.0', 1), "unsupported_claude_integration_version"),
    (b"<!-- AEF:CLAUDE-PROJECT:END -->\n", "ambiguous_claude_managed_block"),
])
def test_unsafe_managed_blocks_are_blocked_without_mutation(content, reason):
    source = project()
    before = deepcopy(source)
    result = plan_claude_integration(source, content)
    assert result[0] == "BLOCKED"
    assert result[2]["reason"] == reason
    assert source == before


def test_incompatible_or_incomplete_aef_state_is_blocked():
    source = project()
    source["files"].pop(".agent/core/learning.md")
    result = plan_claude_integration(source, None)
    assert result[0] == "BLOCKED"
    assert result[2]["reason"] == "missing_aef_doctrine"


def test_status_is_read_only_even_during_evaluation_recovery():
    source = project()
    source["files"][".agent/state/evaluation-transaction.json"] = {}
    result = plan_claude_integration(
        source, CLAUDE_BRIDGE_BYTES, status_only=True
    )
    assert result[0] == "NO_CHANGE"
    assert result[2]["bridge_healthy"] is True
    assert result[2]["workspace_compatible"] is True


def test_exact_legacy_bridge_updates_without_touching_user_bytes():
    before = b"user\r\n" + b"\n\n" + LEGACY_CLAUDE_BRIDGE_BYTES + b"tail"
    result = plan_claude_integration(project(), before)
    assert result[0] == "CHANGE"
    assert result[2]["desired_bytes"] == (
        b"user\r\n\n\n" + CLAUDE_BRIDGE_BYTES + b"tail"
    )


def test_status_blocks_a_modified_bridge_without_changing_source():
    source = project()
    content = CLAUDE_BRIDGE_BYTES.replace(b"guidance", b"authority", 1)
    result = plan_claude_integration(source, content, status_only=True)
    assert result[0] == "BLOCKED"
    assert result[2]["reason"] == "modified_claude_managed_block"
