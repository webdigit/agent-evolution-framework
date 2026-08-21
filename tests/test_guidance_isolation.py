from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aef import cli


SENTINEL = "SENTINEL_AEF_GUIDANCE_ISOLATION"


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def test_guidance_ignores_exterior_memory_and_host_settings(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"SessionStart": [{"matcher": SENTINEL}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AEF_GUIDANCE_SENTINEL", SENTINEL)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invoke(
        capsys, "--json", "--workspace", str(workspace), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-21T10:00:00Z",
    )
    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(workspace),
        "integrate", "all",
    )
    assert code == 0
    dumped = json.dumps(envelope) + captured.out + captured.err
    assert SENTINEL not in dumped
    assert (workspace / "AGENTS.md").is_file()
    assert not (home / "AGENTS.md").exists()


@pytest.mark.skipif(os.name == "nt" and not hasattr(os, "symlink"), reason="symlink unavailable")
def test_symlink_agents_target_is_blocked(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invoke(
        capsys, "--json", "--workspace", str(workspace), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-21T10:00:00Z",
    )
    exterior = tmp_path / "outside-agents.md"
    exterior.write_text("outside", encoding="utf-8")
    link = workspace / "AGENTS.md"
    try:
        link.symlink_to(exterior)
    except OSError:
        pytest.skip("symlink creation requires privileges")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(workspace),
        "integrate", "agents",
    )
    assert code in {3, 4, 6}
    assert envelope.get("status") in {"BLOCKED", "ERROR"}
