"""Guard tests for confined runtime reads — per-site symlink/FIFO behavior."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from aef.runtime_confined_io import open_confined_read_fd
from aef.runtime_discovery import inspect_venv_tree, read_expected_package_version
from aef.runtime_doctor import diagnose_runtime, resolve_proposed_env_path
from tests.test_runtime_discovery import write_venv
from tests.test_runtime_lot2_ter import _write_declared_aef, _write_jsonschema_wheel


def test_outbound_symlink_site_agent_runtime_requirements(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "6.6.6"}),
        encoding="utf-8",
    )
    agent = workspace / ".agent"
    agent.mkdir()
    req = agent / "runtime-requirements.json"
    req.symlink_to(outside / "runtime-requirements.json")
    info = read_expected_package_version(workspace)
    assert info["status"] == "invalid"
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result.get("expected_package_version") is None
    assert result["decision"] != "OK" or result.get("found_package_version") != "6.6.6"


def test_outbound_symlink_site_declared_env_pyvenv_cfg(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    env = write_venv(workspace / ".aef-venv", kind="posix")
    cfg = env / "pyvenv.cfg"
    cfg.unlink()
    cfg.symlink_to(outside / "pyvenv.cfg")
    assert inspect_venv_tree(env, workspace) == "unknown"
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["discovery_method"] != "declared_env"


def test_outbound_symlink_site_declared_env_version_file(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "_version.py").write_text('__version__ = "6.6.6"\n', encoding="utf-8")
    env = write_venv(workspace / ".aef-venv", kind="posix")
    pkg = _write_declared_aef(env, "1.2.0")
    version_file = pkg / "_version.py"
    version_file.unlink()
    version_file.symlink_to(outside / "_version.py")
    agent = workspace / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result.get("declared_env_mismatch") is None
    assert result.get("found_package_version") != "6.6.6"


def test_outbound_symlink_site_local_wheel_sha256(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = b"wheel-bytes"
    real_wheel = outside / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    real_wheel.write_bytes(payload)
    wheel = workspace / real_wheel.name
    wheel.symlink_to(real_wheel)
    digest = hashlib.sha256(payload).hexdigest()
    (workspace / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    _write_jsonschema_wheel(workspace)
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["offline_basis"] is None
    assert result["network_required"] is True


def test_outbound_symlink_site_checksum_sidecar(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = b"wheel-bytes"
    wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = workspace / f"{wheel.name}.sha256"
    sidecar.write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    sidecar.unlink()
    outside_sidecar = outside / sidecar.name
    outside_sidecar.write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    sidecar.symlink_to(outside_sidecar)
    _write_jsonschema_wheel(workspace)
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["local_artifact"] in {"available_unverified", "absent"}
    assert result["offline_basis"] is None


def test_outbound_symlink_site_dependency_wheel(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = b"wheel-bytes"
    wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (workspace / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    real = outside / "jsonschema-4.22.0-py3-none-any.whl"
    with zipfile.ZipFile(real, "w") as archive:
        archive.writestr("jsonschema-4.22.0.dist-info/METADATA", "Name: jsonschema\n")
        archive.writestr("jsonschema-4.22.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    dep = workspace / "jsonschema-4.22.0-py3-none-any.whl"
    dep.symlink_to(real)
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["network_required"] is True
    assert result["offline_basis"] is None


def test_internal_workspace_symlink_declared_env_is_accepted(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    vendored = workspace / "vendored" / "aef"
    vendored.mkdir(parents=True)
    (vendored / "_version.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    env = write_venv(workspace / ".aef-venv", kind="posix")
    pkg = _write_declared_aef(env, "9.9.9")
    import shutil

    shutil.rmtree(pkg)
    pkg.symlink_to(vendored, target_is_directory=True)
    agent = workspace / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "9.9.9"
    assert result["decision"] == "OK"


def test_broken_external_venv_symlink_blocks_install_proposal(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = tmp_path / "elsewhere" / "target-env"
    (workspace / ".aef-venv").symlink_to(target, target_is_directory=True)
    agent = workspace / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    path, issue = resolve_proposed_env_path(workspace, "compatible")
    assert path is None or str(path.resolve()).startswith(str(workspace.resolve()))
    result = diagnose_runtime(workspace, can_import=lambda: False)
    if result["decision"] == "BLOCKED":
        assert result["blocked_cause"] in {"external_env", "unsafe_install_target"}
    else:
        assert result["install_command"]
        assert str(target) not in result["install_command"]


def test_fifo_declared_env_version_file_does_not_block_doctor(tmp_path, capsys):
    if os.name == "nt":
        pytest.skip("posix fifo witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    env = write_venv(workspace / ".aef-venv", kind="posix")
    pkg = _write_declared_aef(env, "1.2.0")
    version_file = pkg / "_version.py"
    version_file.unlink()
    os.mkfifo(version_file)
    from aef import cli

    code = cli.main(["--json", "--workspace", str(workspace), "doctor"])
    captured = capsys.readouterr()
    assert code in {0, 4, 8}
    assert captured.out.strip().startswith("{")
    assert '"command"' in captured.out


def test_open_confined_read_fd_rejects_fifo(tmp_path):
    if os.name == "nt":
        pytest.skip("posix fifo witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    fifo = workspace / "payload.txt"
    os.mkfifo(fifo)
    assert open_confined_read_fd(workspace, fifo) is None


def test_dependency_wheel_archive_not_opened(tmp_path):
    from aef.runtime_confined_io import dependency_wheel_is_usable

    workspace = tmp_path / "ws"
    workspace.mkdir()
    wheel = workspace / "jsonschema-4.0.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04not-opened")
    assert dependency_wheel_is_usable(workspace, wheel) is True


def test_many_empty_dependency_wheels_do_not_hide_valid_wheel(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = b"wheel-bytes"
    aef_wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    aef_wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (workspace / f"{aef_wheel.name}.sha256").write_text(
        f"{digest}  {aef_wheel.name}\n", encoding="utf-8",
    )
    for index in range(25):
        trap = workspace / f"jsonschema-aaa-{index:02d}-py3-none-any.whl"
        trap.write_bytes(b"")
    good = workspace / "jsonschema-zzz-good-py3-none-any.whl"
    good.write_bytes(b"y")
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["offline_basis"] == "self_attested_checksum"
