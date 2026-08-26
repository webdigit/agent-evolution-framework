"""Runtime guidance door — pure render, stale freshness, no home paths."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from aef.guidance_integration import (
    doors_for_integration,
    inspect_runtime_door,
    plan_runtime_integration,
)
from aef.runtime_doctor import (
    DOCTOR_RESULT_FIELDS,
    diagnose_runtime,
    render_runtime_card,
    wrap_runtime_segment,
)
from tests.test_cli_guidance import init_workspace, invoke
from tests.test_guidance_integration import _project
from tests.test_runtime_discovery import _leaks_home, _string_leaves


def _fixture_doctor(**overrides):
    base = {
        "platform": "linux",
        "architecture": "x86_64",
        "interpreter": "CPython-3.13",
        "discovery_method": "python_module",
        "found_package_version": "2.0.0",
        "expected_package_version": "2.0.0",
        "running_module_version": "2.0.0",
        "declared_version_source": None,
        "declared_env_root": None,
        "declared_env_mismatch": None,
        "workspace_compatible": True,
        "venv_status": "absent",
        "network_required": False,
        "local_artifact": "absent",
        "install_command": "",
        "decision": "OK",
        "blocked_cause": None,
        "blocked_path": None,
        "observations": ["declared_env_tree_read:unverified"],
        "offline_basis": None,
    }
    base.update(overrides)
    return base


def test_doors_for_integration_includes_runtime():
    assert doors_for_integration("all") == [
        "agents", "claude", "gemini", "runtime",
    ]
    assert doors_for_integration("runtime") == ["runtime"]


def test_render_runtime_card_is_pure_and_deterministic():
    doctor = _fixture_doctor()
    first = render_runtime_card(doctor)
    second = render_runtime_card(dict(doctor))
    assert first == second
    assert "Trust : tree read only (pip install not verified)" in first
    assert "périmé" in first
    assert "aef integrate runtime" in first
    assert "No install action is required" in first
    for field in DOCTOR_RESULT_FIELDS:
        assert f"`{field}`:" in first
    # Dict order must not affect output
    mismatch = {"version": "1.0.0", "path": ".aef-venv"}
    a = render_runtime_card(_fixture_doctor(declared_env_mismatch=dict(mismatch)))
    b = render_runtime_card(
        _fixture_doctor(declared_env_mismatch={"path": ".aef-venv", "version": "1.0.0"})
    )
    assert a == b


def test_render_runtime_card_no_home_path(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("doctor must not spawn")
    ))
    home = Path.home().resolve()
    workspace = tmp_path
    if home not in workspace.resolve().parents and workspace.resolve() != home:
        workspace = home / ".aef-pytest-runtime-card-home" / "project"
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
    fake_python = home / "bin" / "secret-python"
    monkeypatch.setattr(sys, "executable", str(fake_python))
    try:
        result = diagnose_runtime(workspace, can_import=lambda: False)
        card = render_runtime_card(result)
        wrapped = wrap_runtime_segment(card)
        assert not _leaks_home(card, home)
        assert not _leaks_home(wrapped.decode("utf-8"), home)
        assert not any(_leaks_home(leaf, home) for leaf in _string_leaves(result))
        assert str(fake_python) not in card
        assert sys.executable not in card
    finally:
        planted = home / ".aef-pytest-runtime-card-home"
        if planted.exists():
            shutil.rmtree(planted, ignore_errors=True)


def test_stale_is_not_modified_and_status_does_not_write():
    expected = wrap_runtime_segment(render_runtime_card(_fixture_doctor()))
    stale = expected.replace(b"CPython-3.13", b"CPython-3.12", 1)
    inspection = inspect_runtime_door(stale, expected)
    assert inspection["state"] == "stale"
    assert inspection["freshness"] == "stale"
    assert inspection["state"] != "modified"

    status, _, meta = plan_runtime_integration(
        _project(), stale, expected_bytes=expected, status_only=True,
    )
    assert status == "NO_CHANGE"
    assert meta["reason"] == "runtime_card_stale"
    assert meta["freshness"] == "stale"
    assert meta["bridge_healthy"] is False
    assert meta.get("desired_bytes") == stale

    status2, _, meta2 = plan_runtime_integration(
        _project(), stale, expected_bytes=expected,
    )
    assert status2 == "CHANGE"
    assert meta2["bridge"]["state"] == "stale"
    assert meta2["desired_bytes"] == expected


def test_runtime_drift_plans_change_without_modified_reason():
    expected = wrap_runtime_segment(render_runtime_card(_fixture_doctor()))
    stale = expected.replace(b"linux", b"windows", 1)
    status, _, meta = plan_runtime_integration(
        _project(), stale, expected_bytes=expected,
    )
    assert status == "CHANGE"
    assert "modified" not in (meta.get("reason") or "")
    assert meta["bridge"]["state"] == "stale"


def test_integrate_runtime_status_dry_run_remove(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "runtime", "--dry-run",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert envelope["dry_run"] is True
    assert not (tmp_path / "docs" / "runtime.md").exists()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "runtime",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    path = tmp_path / "docs" / "runtime.md"
    assert path.is_file()
    content = path.read_bytes()
    assert b"Trust : tree read only" in content
    assert b"AEF:RUNTIME:BEGIN" in content
    assert "périmé".encode("utf-8") in content

    prefix = b"# Operator notes\n"
    path.write_bytes(prefix + content)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "runtime", "--status",
    )
    assert code == 0
    assert envelope["status"] == "NO_CHANGE"
    assert envelope["result"]["doors"]["runtime"]["bridge"]["state"] == "installed"
    assert path.read_bytes().startswith(prefix)

    # Force stale by rewriting body between markers
    path.write_bytes(
        path.read_bytes().replace(b"Trust : tree read only", b"Trust : forged", 1)
    )
    before = path.read_bytes()
    code, envelope, captured = invoke(
        capsys, "--workspace", str(tmp_path),
        "integrate", "runtime", "--status",
    )
    assert code == 0
    assert path.read_bytes() == before
    human = captured.out
    assert "périmé" in human or "stale" in human.lower()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "runtime", "--status",
    )
    assert code == 0
    door = envelope["result"]["doors"]["runtime"]
    assert door["bridge"]["state"] == "stale"
    assert door["freshness"] == "stale"
    assert door["reason"] == "runtime_card_stale"
    assert door["bridge"]["state"] != "modified"
    dumped = json.dumps(envelope["result"]["doors"]["runtime"])
    assert "modified_runtime" not in dumped
    assert '"state": "modified"' not in dumped

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "runtime",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert b"Trust : tree read only" in path.read_bytes()
    assert path.read_bytes().startswith(prefix)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "runtime", "--remove",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    remaining = path.read_bytes()
    assert b"AEF:RUNTIME" not in remaining
    assert remaining.startswith(prefix) or remaining == prefix or remaining == b"# Operator notes\n"
