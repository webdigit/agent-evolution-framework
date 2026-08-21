from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aef import cli
from aef.runtime_discovery import DECISION_INSTALL_REQUIRED, INSTALL_REQUIRED_EXIT
from aef.runtime_doctor import DOCTOR_RESULT_FIELDS as RESULT_FIELDS
from tests.test_runtime_discovery import write_venv


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    payload = captured.out.strip()
    envelope = json.loads(payload) if payload.startswith("{") else None
    return code, envelope, captured


def snapshot_agent(root: Path) -> dict[str, tuple[int, bytes]]:
    files = {}
    agent = root / ".agent"
    if not agent.exists():
        return files
    for path in sorted(item for item in agent.rglob("*") if item.is_file()):
        files[path.relative_to(root).as_posix()] = (
            path.stat().st_mtime_ns, path.read_bytes(),
        )
    return files


def test_json_doctor_reports_ready_runtime(tmp_path, capsys):
    before = list(tmp_path.iterdir())
    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 0
    assert envelope["command"] == "DOCTOR"
    assert envelope["status"] == "PASS"
    assert envelope["ok"] is True
    assert envelope["result"]["decision"] == "OK"
    assert envelope["result"]["discovery_method"] == "python_module"
    assert set(RESULT_FIELDS) <= set(envelope["result"])
    assert captured.err == ""
    assert list(tmp_path.iterdir()) == before


def test_human_and_json_share_the_same_decision(tmp_path, capsys):
    json_code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    human_code = cli.main(["--human", "--workspace", str(tmp_path), "doctor"])
    human = capsys.readouterr()
    assert json_code == human_code == 0
    assert envelope["result"]["decision"] == "OK"
    assert "[OK] AEF runtime is ready" in human.out
    assert "INSTALL_REQUIRED" not in human.out


def test_doctor_does_not_mutate_agent_or_existing_venv(tmp_path, capsys, monkeypatch):
    write_venv(
        tmp_path / ".venv",
        kind="posix" if __import__("os").name != "nt" else "windows",
    )
    marker = tmp_path / ".venv" / "KEEP"
    marker.write_text("owned\n", encoding="utf-8")
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "init", "--instance-id", "agent-1", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    before_agent = snapshot_agent(tmp_path)
    before_venv = marker.read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 0
    assert envelope["status"] == "PASS"
    assert snapshot_agent(tmp_path) == before_agent
    assert marker.read_bytes() == before_venv


def test_install_required_parity_without_pip(tmp_path, capsys, monkeypatch):
    result = {
        "platform": "linux",
        "architecture": "x86_64",
        "interpreter": "CPython-3.11.0",
        "discovery_method": "none",
        "found_package_version": None,
        "expected_package_version": "1.2.0",
        "workspace_compatible": False,
        "venv_status": "incompatible",
        "network_required": True,
        "local_artifact": "absent",
        "human_action_required": True,
        "install_command": "python3 -m venv .aef-venv && .aef-venv/bin/python -m pip install \"agent-evolution-framework==1.2.0\"",
        "decision": DECISION_INSTALL_REQUIRED,
    }
    monkeypatch.setattr("aef.cli.diagnose_runtime", lambda _workspace, **_hooks: dict(result))

    def fail_run(*_args, **_kwargs):
        raise AssertionError("doctor without --install must not spawn pip")

    monkeypatch.setattr(subprocess, "run", fail_run)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == INSTALL_REQUIRED_EXIT
    assert envelope["status"] == DECISION_INSTALL_REQUIRED
    assert envelope["ok"] is False
    assert envelope["result"]["decision"] == DECISION_INSTALL_REQUIRED
    assert envelope["result"]["install_command"].startswith("python3 -m venv")
    human_code = cli.main(["--human", "--workspace", str(tmp_path), "doctor"])
    human = capsys.readouterr()
    assert human_code == INSTALL_REQUIRED_EXIT
    assert "[INSTALL_REQUIRED]" in human.out
    assert "agent-evolution-framework==1.2.0" in human.out


def test_uninitialized_workspace_is_not_an_audit_failure(tmp_path, capsys):
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 0
    assert envelope["status"] != "FAIL"
    assert envelope["result"]["workspace_compatible"] is False


def test_incompatible_venv_fixture_is_reported_and_preserved(tmp_path, capsys, monkeypatch):
    foreign = "posix" if __import__("os").name == "nt" else "windows"
    write_venv(tmp_path / ".venv", kind=foreign)
    keep = (tmp_path / ".venv" / "pyvenv.cfg").read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 0
    assert envelope["result"]["venv_status"] == "incompatible"
    assert (tmp_path / ".venv" / "pyvenv.cfg").read_bytes() == keep


def test_external_env_is_blocked(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    try:
        (workspace / ".venv").symlink_to(outside, target_is_directory=True)
    except OSError:
        import pytest
        pytest.skip("symlink creation is not available")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(workspace), "doctor",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert list(outside.iterdir()) == []


def test_version_flag_remains_argparse(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr()
    assert output.out.startswith("aef ")
    assert output.err == ""


def test_python_module_subprocess_doctor(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(tmp_path), "doctor"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    envelope = json.loads(completed.stdout)
    assert envelope["command"] == "DOCTOR"
    assert envelope["status"] == "PASS"
    assert envelope["result"]["discovery_method"] == "python_module"
