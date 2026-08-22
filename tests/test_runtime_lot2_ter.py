"""Lot 2 ter: read-only doctor hardening after install surface removal."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import is_pep440_version_token, read_expected_package_version
from aef.runtime_doctor import diagnose_runtime
from tests.test_runtime_discovery import no_path, write_venv


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


@pytest.mark.parametrize(
    "version",
    ["1.2", "2026.1", "1!1.2.0", "1.2.0", "1.2.0rc1"],
)
def test_pep440_versions_accepted(version):
    assert is_pep440_version_token(version)


@pytest.mark.parametrize(
    "version",
    ['1.0.0" & echo PWNED & "', "1.0.0;rm", "1 2 3", "1.2.0rc١", "1.2.0.zzz"],
)
def test_shell_unsafe_versions_rejected(version):
    assert not is_pep440_version_token(version)


def test_two_component_wheel_visible(tmp_path):
    wheel = tmp_path / "agent_evolution_framework-1.2-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["local_artifact"] in {"available_unverified", "checksum_matched", "absent"}
    assert result["decision"] == "INSTALL_REQUIRED"


def test_install_command_uses_workspace_absolute_venv(tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    agent = workspace / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(workspace), "doctor",
    )
    assert code == 8
    proposed = envelope["result"]["install_command"]
    abs_venv = str((workspace / ".aef-venv").resolve())
    norm_proposed = proposed.replace("\\", "/")
    norm_venv = abs_venv.replace("\\", "/")
    assert norm_venv in norm_proposed
    assert ".aef-venv" in norm_proposed
    assert norm_proposed.count(".aef-venv") >= 2 or norm_venv in norm_proposed


def test_symlinked_version_outside_workspace_not_read(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    huge = outside / "huge_version.py"
    huge.write_text('__version__ = "1.2.0"\n' + ("x" * (11 * 1024 * 1024)), encoding="utf-8")
    kind = "posix"
    env = write_venv(workspace / ".aef-venv", kind=kind)
    pkg = env / "lib/python3.11/site-packages/aef"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    version_file = pkg / "_version.py"
    version_file.symlink_to(huge)
    start = time.perf_counter()
    result = diagnose_runtime(workspace, can_import=lambda: False)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0
    assert result["discovery_method"] == "none"
    assert result["found_package_version"] is None


def test_ambiguous_wheels_observed_not_blocked_when_runtime_ok(tmp_path, monkeypatch):
    (tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl").write_bytes(b"a")
    (tmp_path / "agent_evolution_framework-1.3.0-py3-none-any.whl").write_bytes(b"b")
    monkeypatch.setattr("aef.runtime_discovery.module_importable", lambda: True)
    result = diagnose_runtime(tmp_path)
    assert result["decision"] == "OK"
    assert result["local_artifact"] == "ambiguous"
    assert "ambiguous_local_wheels" in result["observations"]
    assert result["blocked_cause"] is None


def test_self_attested_checksum_disclosed_for_offline(tmp_path, capsys):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-9.9.9-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    (tmp_path / "jsonschema-4.22.0-py3-none-any.whl").write_bytes(b"dep")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["local_artifact"] == "checksum_matched"
    assert result["network_required"] is False
    assert result["offline_basis"] == "self_attested_checksum"
    assert "--no-index" in result["install_command"]

    code, _, captured = invoke(
        capsys, "--human", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 8
    assert "checksum_matched" in captured.out
    assert "self_attested_checksum" in captured.out
    assert "Network   : no" in captured.out


def test_jsonschema_specifications_wheel_does_not_enable_offline(tmp_path):
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    (tmp_path / "jsonschema-specifications-2024.10.1-py3-none-any.whl").write_bytes(b"x")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["network_required"] is True
    assert result["offline_basis"] is None


def test_install_command_targets_free_env_when_primary_occupied(tmp_path, capsys):
    workspace = tmp_path / "project"
    workspace.mkdir()
    write_venv(workspace / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    agent = workspace / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(workspace), "doctor",
    )
    assert code == 8
    proposed = envelope["result"]["install_command"]
    platform_env = (workspace / f".aef-venv-{'windows' if os.name == 'nt' else 'linux'}").resolve()
    primary_env = (workspace / ".aef-venv").resolve()
    quoted = proposed.split("&&", 1)[0]
    target_token = quoted.replace('"', "").split()[-1]
    target_path = Path(target_token).resolve()
    assert target_path == platform_env
    assert target_path != primary_env


def test_namespace_package_declared_env_is_discovered(tmp_path):
    env = write_venv(tmp_path / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    pkg = env / ("Lib" if os.name == "nt" else "lib/python3.11") / "site-packages" / "aef"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "_version.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "1.2.0"
    assert result["decision"] == "OK"


def test_blocked_human_shows_cause_and_path(tmp_path, capsys):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text("{not-json", encoding="utf-8")
    code, _, captured = invoke(capsys, "--human", "--workspace", str(tmp_path), "doctor")
    assert code == 4
    assert "invalid_expected_package_version" in captured.out
    assert "runtime-requirements.json" in captured.out
    assert "external environment path" not in captured.out


def _lying_declared_env(workspace: Path) -> Path:
    """Minimal venv tree: only _version.py, no real package install."""
    kind = "windows" if os.name == "nt" else "posix"
    env = write_venv(workspace / ".aef-venv", kind=kind)
    pkg = env / ("Lib" if os.name == "nt" else "lib/python3.11") / "site-packages" / "aef"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "_version.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    return env


def test_lying_declared_env_reports_tree_version_with_reserve(tmp_path, capsys, monkeypatch):
    """Hand-crafted _version.py without a real install: PASS + visible reserve.

    Doctor does not execute the declared venv and does not attest pip success.
    It reports what the workspace tree declares and flags the tree-read source.
    """
    _lying_declared_env(tmp_path)
    monkeypatch.setattr("aef._version.__version__", "1.2.0")
    result = diagnose_runtime(tmp_path)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "9.9.9"
    assert result["decision"] == "OK"
    assert "declared_env_version_from_tree" in result["observations"]

    code, _, captured = invoke(
        capsys, "--human", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 0
    assert "declared_env_version_from_tree" in captured.out
    assert "[OK]" in captured.out


def test_unverified_wheel_never_sets_offline_basis(tmp_path):
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["local_artifact"] == "available_unverified"
    assert result["offline_basis"] is None
    assert result["network_required"] is True


def test_doctor_has_no_install_flags(capsys):
    parser = cli._build_parser()
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["doctor", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--install" not in output
    assert "--reuse-env" not in output
    assert "manually" in output.lower() or "read-only" in output.lower()


def test_invalid_expected_version_still_blocked(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "1.2"}),
        encoding="utf-8",
    )
    info = read_expected_package_version(tmp_path)
    assert info["status"] == "valid"
    assert info["value"] == "1.2"
