from __future__ import annotations

import json
from pathlib import Path

from aef import cli
from aef.guidance_integration import AGENTS_BYTES, CLAUDE_ROOT_BYTES, GEMINI_BYTES


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def init_workspace(tmp_path, capsys):
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-21T10:00:00Z",
    )
    assert code == 0
    return tmp_path


def test_agents_dry_run_writes_nothing(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "agents", "--dry-run",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert envelope["dry_run"] is True
    assert not (tmp_path / "AGENTS.md").exists()


def test_all_install_replay_and_remove(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "all",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert (tmp_path / "AGENTS.md").read_bytes() == AGENTS_BYTES
    assert (tmp_path / "CLAUDE.md").read_bytes() == CLAUDE_ROOT_BYTES
    assert (tmp_path / "GEMINI.md").read_bytes() == GEMINI_BYTES
    assert (tmp_path / "docs" / "runtime.md").is_file()
    assert b"AEF:RUNTIME:BEGIN" in (tmp_path / "docs" / "runtime.md").read_bytes()
    assert (tmp_path / "docs" / "knowledge.md").is_file()
    assert b"AEF:LEARNING:BEGIN" in (tmp_path / "docs" / "knowledge.md").read_bytes()
    assert not (tmp_path / ".claude").exists()

    code2, envelope2, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "all",
    )
    assert code2 == 0
    assert envelope2["status"] == "NO_CHANGE"

    code3, envelope3, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "gemini", "--remove",
    )
    assert code3 == 0
    assert envelope3["status"] == "CHANGE"
    assert (tmp_path / "AGENTS.md").read_bytes() == AGENTS_BYTES
    assert (tmp_path / "CLAUDE.md").read_bytes() == CLAUDE_ROOT_BYTES
    assert (tmp_path / "GEMINI.md").read_bytes() == b""
    assert (tmp_path / "docs" / "runtime.md").is_file()


def test_claude_install_does_not_rewrite_legacy_bridge(tmp_path, capsys):
    from aef.claude_integration import CLAUDE_BRIDGE_BYTES

    init_workspace(tmp_path, capsys)
    legacy = tmp_path / ".claude" / "CLAUDE.md"
    legacy.parent.mkdir()
    legacy.write_bytes(CLAUDE_BRIDGE_BYTES)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "claude",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert legacy.read_bytes() == CLAUDE_BRIDGE_BYTES
    assert (tmp_path / "CLAUDE.md").read_bytes() == CLAUDE_ROOT_BYTES
    assert (tmp_path / "AGENTS.md").read_bytes() == AGENTS_BYTES


def test_claude_status_sees_legacy_without_mutation(tmp_path, capsys):
    from aef.claude_integration import CLAUDE_BRIDGE_BYTES

    init_workspace(tmp_path, capsys)
    legacy = tmp_path / ".claude" / "CLAUDE.md"
    legacy.parent.mkdir()
    legacy.write_bytes(CLAUDE_BRIDGE_BYTES)
    before = legacy.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "claude", "--status",
    )
    assert code == 0
    assert envelope["status"] == "NO_CHANGE"
    assert envelope["result"]["installed"] is True
    assert envelope["meta"]["legacy_bridge"]["state"] == "installed"
    assert legacy.read_bytes() == before


def test_upgrade_does_not_create_gemini(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    invoke(capsys, "--json", "--workspace", str(tmp_path), "upgrade")
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "GEMINI.md").exists()


def test_preserve_user_prose_on_agents_install(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    (tmp_path / "AGENTS.md").write_bytes(b"# User prose\n")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "agents",
    )
    assert code == 0
    content = (tmp_path / "AGENTS.md").read_bytes()
    assert content.startswith(b"# User prose\n\n\n")
    assert AGENTS_BYTES in content
