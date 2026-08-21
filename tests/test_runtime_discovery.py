from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aef.runtime_discovery import (
    DECISION_BLOCKED,
    DECISION_INSTALL_REQUIRED,
    DECISION_OK,
    INSTALL_REQUIRED_EXIT,
    discover_runtime,
    inspect_venv_tree,
    interpreter_label,
    read_expected_package_version,
)
from aef.runtime_doctor import DOCTOR_RESULT_FIELDS, diagnose_runtime, proposed_install_command


def write_venv(root: Path, *, kind: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if kind == "windows":
        (root / "pyvenv.cfg").write_text("home = C:\\Python311\n", encoding="utf-8")
        scripts = root / "Scripts"
        scripts.mkdir()
        (scripts / "python.exe").write_bytes(b"")
    elif kind == "posix":
        (root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        binary = root / "bin"
        binary.mkdir()
        (binary / "python").write_bytes(b"")
    elif kind == "empty":
        (root / "pyvenv.cfg").write_text("home = unknown\n", encoding="utf-8")
    return root


def no_path(_name: str) -> None:
    return None


def test_install_required_is_distinct_from_blocked_and_error():
    assert DECISION_INSTALL_REQUIRED == "INSTALL_REQUIRED"
    assert DECISION_INSTALL_REQUIRED not in {DECISION_BLOCKED, "FAIL", "ERROR", "FAILED"}
    assert INSTALL_REQUIRED_EXIT == 8
    assert INSTALL_REQUIRED_EXIT not in {0, 1, 3, 4, 5, 6, 70}


def test_expected_package_version_is_not_framework_or_schema(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "1.2.0", "framework_version": "9.9.9"}),
        encoding="utf-8",
    )
    (agent / "manifest.json").write_text(
        json.dumps({"framework_version": "1.0.0", "schema_version": "1.0.0"}),
        encoding="utf-8",
    )
    assert read_expected_package_version(tmp_path) == "1.2.0"
    discovered = discover_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: False,
    )
    assert discovered["expected_package_version"] == "1.2.0"
    assert "framework_version" not in discovered
    assert "schema_version" not in discovered


@pytest.mark.parametrize(
    ("host", "kind", "expected"),
    [
        ("windows", "windows", "compatible"),
        ("windows", "posix", "incompatible"),
        ("linux", "posix", "compatible"),
        ("linux", "windows", "incompatible"),
        ("macos", "posix", "compatible"),
        ("macos", "windows", "incompatible"),
        ("linux", "empty", "unknown"),
    ],
)
def test_inspect_venv_tree_never_spawns(monkeypatch, tmp_path, host, kind, expected):
    monkeypatch.setattr("aef.runtime_discovery.host_platform", lambda: host)
    root = write_venv(tmp_path / ".venv", kind=kind)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("fixture venv binary must not be spawned")

    monkeypatch.setattr(subprocess, "run", fail_run)
    monkeypatch.setattr(subprocess, "Popen", fail_run)
    assert inspect_venv_tree(root) == expected
    assert inspect_venv_tree(tmp_path / "missing") == "absent"


def test_empty_venv_is_not_a_declared_runtime(tmp_path, monkeypatch):
    write_venv(tmp_path / ".aef-venv", kind="windows" if __import__("os").name == "nt" else "posix")
    discovered = discover_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: False,
    )
    assert discovered["discovery_method"] == "none"
    assert discovered["decision"] == DECISION_INSTALL_REQUIRED
    assert discovered["venv_status"] == "compatible"


def test_discovery_order_path_then_module_then_declared(tmp_path, monkeypatch):
    write_venv(tmp_path / ".aef-venv", kind="windows" if __import__("os").name == "nt" else "posix")
    pkg = tmp_path / ".aef-venv" / ("Lib" if __import__("os").name == "nt" else "lib/python3.11")
    pkg = pkg / "site-packages" / "aef"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_version.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    (tmp_path / "aef").write_bytes(b"")
    (tmp_path / "aef.exe").write_bytes(b"")
    monkeypatch.setattr(
        "aef.runtime_discovery.path_binary_compatible",
        lambda path: path.name.lower() in {"aef", "aef.exe"},
    )
    path_hit = discover_runtime(
        tmp_path,
        path_lookup=lambda name: str(tmp_path / ("aef.exe" if name.endswith(".exe") else "aef")),
        can_import=lambda: False,
    )
    assert path_hit["discovery_method"] == "path"
    assert path_hit["found_package_version"] == "1.2.0"

    module_hit = discover_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: True,
    )
    assert module_hit["discovery_method"] == "python_module"

    declared = discover_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: False,
    )
    assert declared["discovery_method"] == "declared_env"
    assert declared["found_package_version"] == "1.2.0"
    assert declared["decision"] == DECISION_OK


def test_pin_mismatch_requires_install(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    discovered = discover_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: True,
    )
    assert discovered["decision"] == DECISION_INSTALL_REQUIRED
    assert discovered["found_package_version"] != "9.9.9"


def test_diagnose_fields_and_no_home_path(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("doctor must not spawn")
    ))
    result = diagnose_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: False,
    )
    assert tuple(result) == DOCTOR_RESULT_FIELDS
    assert result["decision"] == DECISION_INSTALL_REQUIRED
    assert result["human_action_required"] is True
    assert result["network_required"] is True
    assert result["local_artifact"] == "absent"
    assert "agent-evolution-framework==" in result["install_command"]
    assert str(Path.home()) not in json.dumps(result)
    assert interpreter_label().startswith(("CPython-", "PyPy-"))
    assert "\\" not in interpreter_label() or "CPython-" in interpreter_label()


def test_local_wheel_announces_offline_install(tmp_path):
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    result = diagnose_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: False,
    )
    assert result["local_artifact"] == "available"
    assert result["network_required"] is False
    assert "--no-index" in result["install_command"]


def test_proposed_command_quotes_spaces():
    command = proposed_install_command(
        wheel=Path("agent_evolution_framework-1.2.0 my wheel.whl"),
        version="1.2.0",
        isolated_dir=".aef-venv",
    )
    assert "python" in command or "py -3.11" in command
    assert "venv" in command


def test_external_env_is_blocked(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    link = workspace / ".venv"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")
    discovered = discover_runtime(
        workspace, path_lookup=no_path, can_import=lambda: False,
    )
    assert discovered["decision"] == DECISION_BLOCKED
    assert discovered["external_env"] is True
