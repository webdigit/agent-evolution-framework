"""Guard tests for confined runtime reads — symlink attacks and install safety."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from aef.runtime_discovery import discover_runtime, inspect_venv_tree
from aef.runtime_doctor import diagnose_runtime, resolve_proposed_env_path
from tests.test_runtime_discovery import write_venv
from tests.test_runtime_lot2_ter import _write_declared_aef, _write_jsonschema_wheel


def test_symlinked_pyvenv_cfg_outside_workspace_is_not_read(tmp_path):
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


def test_symlinked_dependency_wheel_outside_workspace_disables_offline(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payload = b"wheel-bytes"
    wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    import hashlib

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


def test_fifo_console_script_does_not_block_doctor(tmp_path, capsys):
    if os.name == "nt":
        pytest.skip("posix fifo witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    env = write_venv(workspace / ".aef-venv", kind="posix")
    _write_declared_aef(env, "1.2.0")
    console = env / "bin" / "aef"
    if console.exists():
        console.unlink()
    os.mkfifo(console)
    from aef import cli

    code = cli.main(["--json", "--workspace", str(workspace), "doctor"])
    captured = capsys.readouterr()
    assert code in {0, 4, 8}
    assert captured.out.strip().startswith("{")
    assert '"command"' in captured.out


def test_zip_bomb_wheel_does_not_crash_doctor(tmp_path, monkeypatch):
    from aef.runtime_confined_io import MAX_ZIP_MEMBER_BYTES, dependency_wheel_is_usable

    workspace = tmp_path / "ws"
    workspace.mkdir()
    wheel = workspace / "jsonschema-4.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("jsonschema-4.0.0.dist-info/METADATA", "Name: jsonschema\n")
        archive.writestr("jsonschema-4.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")

    original_getinfo = zipfile.ZipFile.getinfo

    def huge_metadata(self, name):
        info = original_getinfo(self, name)
        if name.endswith("/METADATA"):
            info.file_size = MAX_ZIP_MEMBER_BYTES + 1
        return info

    monkeypatch.setattr(zipfile.ZipFile, "getinfo", huge_metadata)
    assert dependency_wheel_is_usable(workspace, wheel) is False

    payload = b"wheel-bytes"
    aef_wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    aef_wheel.write_bytes(payload)
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    (workspace / f"{aef_wheel.name}.sha256").write_text(
        f"{digest}  {aef_wheel.name}\n", encoding="utf-8",
    )
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["offline_basis"] is None
    assert result["decision"] in {"OK", "INSTALL_REQUIRED", "BLOCKED"}


def test_symlinked_console_script_outside_workspace_is_ignored(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload").write_bytes(b"x" * 128)
    env = write_venv(workspace / ".aef-venv", kind="posix")
    _write_declared_aef(env, "1.2.0")
    console = env / "bin" / "aef"
    if console.exists():
        console.unlink()
    console.symlink_to(outside / "payload")
    result = diagnose_runtime(workspace, can_import=lambda: False)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "1.2.0"
    assert "declared_env_tree_read:unverified" in result["observations"]


def test_jsonschema_wheel_rejects_wrong_distribution_name(tmp_path):
    from aef.runtime_confined_io import dependency_wheel_is_usable

    wheel = tmp_path / "jsonschema-evil-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("jsonschema-evil.dist-info/METADATA", "Name: jsonschemaXXX-evil\n")
        archive.writestr("jsonschema-evil.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    assert dependency_wheel_is_usable(tmp_path, wheel) is False
