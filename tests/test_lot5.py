"""Lot 5 — guidance fences, atomic integrate all, modes, TOCTOU, envelopes."""

from __future__ import annotations

import stat

import pytest

from aef.filesystem import EVALUATION_TRANSACTION_PATH, file_is_readonly
from aef.guidance_integration import (
    AGENTS_BYTES,
    AGENTS_END_MARKER,
    CLAUDE_ROOT_BYTES,
    GEMINI_BYTES,
    inspect_door,
    plan_door_integration,
)
from tests.test_cli_guidance import init_workspace, invoke
from tests.test_cli_record import submission, write_recording
from tests.test_guidance_integration import _project


DOORS = (
    ("agents", "AGENTS.md", AGENTS_BYTES),
    ("claude", "CLAUDE.md", CLAUDE_ROOT_BYTES),
    ("gemini", "GEMINI.md", GEMINI_BYTES),
)


def _fence(payload: bytes, mark: bytes = b"```") -> bytes:
    return b"Example:\n" + mark + b"markdown\n" + payload + mark + b"\n"


def _indent(payload: bytes) -> bytes:
    return b"".join(b"    " + line + b"\n" for line in payload.splitlines())


@pytest.mark.parametrize("door,filename,segment", DOORS)
def test_fenced_quote_is_absent_on_status_install_and_remove(
    tmp_path, capsys, door, filename, segment,
):
    init_workspace(tmp_path, capsys)
    path = tmp_path / filename
    quoted = _fence(segment)
    path.write_bytes(quoted)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", door, "--status",
    )
    assert code == 0
    assert envelope["status"] == "NO_CHANGE"
    assert envelope["result"]["installed"] is False
    assert envelope["result"]["doors"][door]["bridge"]["state"] == "absent"

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", door,
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    after = path.read_bytes()
    assert quoted in after
    assert inspect_door(door, after)["state"] == "installed"

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", door, "--remove",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    remaining = path.read_bytes()
    assert quoted in remaining
    assert inspect_door(door, remaining)["state"] == "absent"


@pytest.mark.parametrize("door,filename,segment", DOORS)
def test_tilde_fence_and_unclosed_fence_are_not_markers(
    tmp_path, capsys, door, filename, segment,
):
    init_workspace(tmp_path, capsys)
    path = tmp_path / filename
    path.write_bytes(_fence(segment, mark=b"~~~"))
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", door, "--status",
    )
    assert envelope["result"]["doors"][door]["bridge"]["state"] == "absent"
    invoke(capsys, "--json", "--workspace", str(tmp_path), "integrate", door)
    assert inspect_door(door, path.read_bytes())["state"] == "installed"

    path.write_bytes(b"# docs\n```\n" + segment)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", door, "--status",
    )
    assert envelope["result"]["doors"][door]["bridge"]["state"] == "absent"
    invoke(capsys, "--json", "--workspace", str(tmp_path), "integrate", door)
    installed = path.read_bytes()
    assert inspect_door(door, installed)["state"] == "installed"
    assert b"# docs\n" in installed
    assert b"```\n" + segment in installed or segment in installed


@pytest.mark.parametrize("door,filename,segment", DOORS)
def test_four_space_indented_marker_is_not_a_marker(
    tmp_path, capsys, door, filename, segment,
):
    init_workspace(tmp_path, capsys)
    path = tmp_path / filename
    path.write_bytes(_indent(segment))
    _, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", door, "--status",
    )
    assert envelope["result"]["doors"][door]["bridge"]["state"] == "absent"
    invoke(capsys, "--json", "--workspace", str(tmp_path), "integrate", door)
    assert inspect_door(door, path.read_bytes())["state"] == "installed"
    invoke(capsys, "--json", "--workspace", str(tmp_path), "integrate", door, "--remove")
    assert _indent(segment) in path.read_bytes()
    assert inspect_door(door, path.read_bytes())["state"] == "absent"


def test_blocked_agents_door_blocks_integrate_all_without_writing_doorbells(
    tmp_path, capsys,
):
    init_workspace(tmp_path, capsys)
    (tmp_path / "AGENTS.md").write_bytes(b"notes\n" + AGENTS_END_MARKER + b"\n")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "all",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["ok"] is False
    assert envelope["result"]["bridge_healthy"] is False
    assert envelope["result"]["doors"]["agents"]["status"] == "BLOCKED"
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "GEMINI.md").exists()
    assert (tmp_path / "AGENTS.md").read_bytes().startswith(b"notes\n")


def test_guidance_replace_preserves_mode_and_refuses_readonly(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b"# notes\n")
    path.chmod(0o644)
    before = stat.S_IMODE(path.stat().st_mode)
    invoke(capsys, "--json", "--workspace", str(tmp_path), "integrate", "agents")
    assert stat.S_IMODE(path.stat().st_mode) == before

    path.chmod(0o444)
    if not file_is_readonly(path):
        pytest.skip("readonly bit is not enforceable on this platform")
    snapshot = path.read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "agents", "--remove",
    )
    assert code != 0
    assert path.read_bytes() == snapshot


def test_cli_uses_one_snapshot_when_file_changes_after_first_read(
    tmp_path, capsys, monkeypatch,
):
    init_workspace(tmp_path, capsys)
    path = tmp_path / "AGENTS.md"
    path.write_bytes(b"# notes\n")
    original = __import__("aef.cli", fromlist=["read_guidance_file"]).read_guidance_file
    state = {"n": 0}

    def wrapped(root, relative):
        data = original(root, relative)
        if relative == "AGENTS.md":
            state["n"] += 1
            if state["n"] == 1:
                path.write_bytes(b"# concurrent edit\n")
        return data

    monkeypatch.setattr("aef.cli.read_guidance_file", wrapped)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "agents",
    )
    assert path.read_bytes() == b"# concurrent edit\n"
    assert code != 0
    assert envelope["status"] in {"ERROR", "BLOCKED"}


def test_missing_final_newline_is_installed_and_removable():
    stripped = AGENTS_BYTES[:-1] if AGENTS_BYTES.endswith(b"\n") else AGENTS_BYTES
    assert inspect_door("agents", stripped)["state"] == "installed"
    status, _, meta = plan_door_integration(_project(), stripped, door="agents")
    assert status == "NO_CHANGE"
    status, _, meta = plan_door_integration(
        _project(), stripped, door="agents", remove=True,
    )
    assert status == "CHANGE"
    assert meta["desired_bytes"] == b""


def test_record_with_evaluation_journal_is_blocked_not_error(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    journal = tmp_path.joinpath(*EVALUATION_TRANSACTION_PATH.split("/"))
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}", encoding="utf-8")
    recording = write_recording(tmp_path / "recording.json", submission())
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["error"] is None
    assert envelope["meta"]["reason"] == "evaluation_recovery_required"
