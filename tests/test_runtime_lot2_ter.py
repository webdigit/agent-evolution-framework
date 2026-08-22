"""Lot 2 ter: read-only doctor hardening after install surface removal."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import is_pep440_version_token, read_expected_package_version
from aef.runtime_doctor import diagnose_runtime, resolve_proposed_env_path
from tests.test_runtime_discovery import write_venv


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def _pkg_root(env: Path) -> Path:
    return env / ("Lib" if os.name == "nt" else "lib/python3.11") / "site-packages" / "aef"


def _write_declared_aef(env: Path, version: str) -> Path:
    pkg = _pkg_root(env)
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "_version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    return pkg


def _write_real_install_markers(env: Path, pkg: Path, *, version: str = "1.2.0") -> None:
    site_packages = pkg.parent
    dist = site_packages / f"agent_evolution_framework-{version}.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text("Name: agent-evolution-framework\n", encoding="utf-8")
    (dist / "WHEEL").write_text("Wheel-Version: 1.0\n", encoding="utf-8")
    (dist / "RECORD").write_text("aef/_version.py,,\n", encoding="utf-8")
    if os.name == "nt":
        scripts = env / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "aef.exe").write_bytes(b"x")
    else:
        binary = env / "bin"
        binary.mkdir(parents=True, exist_ok=True)
        (binary / "aef").write_bytes(b"x")


def _write_jsonschema_wheel(directory: Path) -> Path:
    dep = directory / "jsonschema-4.22.0-py3-none-any.whl"
    with zipfile.ZipFile(dep, "w") as archive:
        archive.writestr("jsonschema-4.22.0.dist-info/METADATA", "Name: jsonschema\n")
        archive.writestr("jsonschema-4.22.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    return dep


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


def test_symlinked_version_outside_workspace_not_read(tmp_path):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    huge = outside / "huge_version.py"
    huge.write_text('__version__ = "1.2.0"\n' + ("x" * (11 * 1024 * 1024)), encoding="utf-8")
    env = write_venv(workspace / ".aef-venv", kind="posix")
    pkg = _pkg_root(env)
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_version.py").symlink_to(huge)
    start = time.perf_counter()
    result = diagnose_runtime(workspace, can_import=lambda: False)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0
    assert result["discovery_method"] == "none"
    assert result["found_package_version"] is None


@pytest.mark.parametrize(
    "link_target",
    ["aef", "site-packages", "lib", "lib/python3.11"],
)
def test_declared_env_intermediate_symlink_is_not_read(tmp_path, link_target):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_pkg = outside / "aef"
    outside_pkg.mkdir()
    (outside_pkg / "_version.py").write_text('__version__ = "6.6.6"\n', encoding="utf-8")
    env = write_venv(workspace / ".aef-venv", kind="posix")
    lib = env / "lib"
    pydir = lib / "python3.11"
    site_packages = pydir / "site-packages"
    site_packages.mkdir(parents=True)
    pkg = site_packages / "aef"
    pkg.mkdir()
    (pkg / "_version.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    if link_target == "aef":
        shutil.rmtree(pkg)
        (site_packages / "aef").symlink_to(outside_pkg, target_is_directory=True)
    elif link_target == "site-packages":
        outside_site = outside / "site-packages"
        outside_site.mkdir(parents=True)
        (outside_site / "aef").mkdir()
        (outside_site / "aef" / "_version.py").write_text('__version__ = "6.6.6"\n', encoding="utf-8")
        shutil.rmtree(site_packages)
        (pydir / "site-packages").symlink_to(outside_site, target_is_directory=True)
    elif link_target == "lib":
        outside_lib = outside / "lib"
        (outside_lib / "python3.11" / "site-packages" / "aef").mkdir(parents=True)
        (outside_lib / "python3.11" / "site-packages" / "aef" / "_version.py").write_text(
            '__version__ = "6.6.6"\n', encoding="utf-8",
        )
        shutil.rmtree(lib)
        (env / "lib").symlink_to(outside_lib, target_is_directory=True)
    elif link_target == "lib/python3.11":
        outside_py = outside / "python3.11"
        (outside_py / "site-packages" / "aef").mkdir(parents=True)
        (outside_py / "site-packages" / "aef" / "_version.py").write_text(
            '__version__ = "6.6.6"\n', encoding="utf-8",
        )
        shutil.rmtree(pydir)
        (lib / "python3.11").symlink_to(outside_py, target_is_directory=True)
    result = diagnose_runtime(workspace, can_import=lambda: False)
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
    _write_jsonschema_wheel(tmp_path)
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


def test_jsonschema_non_zip_wheel_does_not_enable_offline(tmp_path):
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    fake_dep = tmp_path / "jsonschema-4.22.0-py3-none-any.whl"
    fake_dep.write_bytes(b"not-a-zip-wheel")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["network_required"] is True
    assert result["offline_basis"] is None


def test_jsonschema_zip_wheel_enables_offline(tmp_path):
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    _write_jsonschema_wheel(tmp_path)
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["network_required"] is False
    assert result["offline_basis"] == "self_attested_checksum"


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


def test_install_command_targets_free_env_when_both_primary_names_occupied(tmp_path, capsys):
    workspace = tmp_path / "project"
    workspace.mkdir()
    write_venv(workspace / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    platform_env = workspace / f".aef-venv-{'windows' if os.name == 'nt' else 'linux'}"
    write_venv(platform_env, kind="windows" if os.name == "nt" else "posix")
    agent = workspace / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "9.9.9"}),
        encoding="utf-8",
    )
    proposed_path, issue = resolve_proposed_env_path(workspace, "compatible")
    assert issue is None
    assert not proposed_path.exists()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(workspace), "doctor",
    )
    assert code == 8
    proposed = envelope["result"]["install_command"]
    quoted = proposed.split("&&", 1)[0]
    target_token = quoted.replace('"', "").split()[-1]
    target_path = Path(target_token).resolve()
    assert target_path == proposed_path.resolve()
    assert target_path != (workspace / ".aef-venv").resolve()
    assert target_path != platform_env.resolve()


def test_namespace_package_declared_env_is_discovered(tmp_path):
    env = write_venv(tmp_path / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    pkg = _write_declared_aef(env, "1.2.0")
    _write_real_install_markers(env, pkg)
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "1.2.0"
    assert result["decision"] == "OK"
    assert result["declared_env_install_evidence"]


def test_stale_declared_env_skipped_for_satisfying_candidate(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "1.2.0"}),
        encoding="utf-8",
    )
    stale = write_venv(tmp_path / ".aef-venv", kind="posix" if os.name != "nt" else "windows")
    _write_declared_aef(stale, "1.0.0")
    current = write_venv(tmp_path / ".venv", kind="posix" if os.name != "nt" else "windows")
    _write_declared_aef(current, "1.2.0")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "1.2.0"
    assert result["decision"] == "OK"


def test_stale_only_declared_env_falls_back_to_python_module(tmp_path, monkeypatch):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "1.2.0"}),
        encoding="utf-8",
    )
    stale = write_venv(tmp_path / ".aef-venv", kind="posix" if os.name != "nt" else "windows")
    _write_declared_aef(stale, "1.0.0")
    monkeypatch.setattr("aef._version.__version__", "1.2.0")
    result = diagnose_runtime(tmp_path)
    assert result["discovery_method"] == "python_module"
    assert result["found_package_version"] == "1.2.0"
    assert result["decision"] == "OK"
    assert result["declared_env_mismatch"] == {
        "path": ".aef-venv",
        "version": "1.0.0",
    }


def test_satisfying_first_declared_env_is_selected(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": "1.2.0"}),
        encoding="utf-8",
    )
    first = write_venv(tmp_path / ".aef-venv", kind="posix" if os.name != "nt" else "windows")
    _write_declared_aef(first, "1.2.0")
    second = write_venv(tmp_path / ".venv", kind="posix" if os.name != "nt" else "windows")
    _write_declared_aef(second, "1.0.0")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "1.2.0"
    assert result["declared_version_source"].startswith(".aef-venv/")


def test_cli_external_env_blocked_path(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    try:
        (workspace / ".venv").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")
    code, _, captured = invoke(capsys, "--human", "--workspace", str(workspace), "doctor")
    assert code == 4
    assert "external_env" in captured.out
    assert "Path      : .venv" in captured.out


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
    kind = "windows" if os.name == "nt" else "posix"
    env = write_venv(workspace / ".aef-venv", kind=kind)
    _write_declared_aef(env, "9.9.9")
    return env


def test_lying_declared_env_reports_tree_version_with_reserve(tmp_path, capsys, monkeypatch):
    _lying_declared_env(tmp_path)
    monkeypatch.setattr("aef._version.__version__", "1.2.0")
    result = diagnose_runtime(tmp_path)
    assert result["discovery_method"] == "declared_env"
    assert result["found_package_version"] == "9.9.9"
    assert result["running_module_version"] == "1.2.0"
    assert result["declared_version_source"]
    assert result["declared_env_install_evidence"] == []
    assert "declared_env_install_evidence:none" in result["observations"]

    code, _, captured = invoke(
        capsys, "--human", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 0
    assert "Evidence  : none observed (tree-only read)" in captured.out
    assert "Running   : 1.2.0" in captured.out
    assert "Source    :" in captured.out
    assert "[OK]" in captured.out


def test_real_declared_env_human_output_differs_from_fabricated(tmp_path, capsys, monkeypatch):
    env = write_venv(tmp_path / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    pkg = _write_declared_aef(env, "1.2.0")
    _write_real_install_markers(env, pkg)
    monkeypatch.setattr("aef._version.__version__", "1.2.0")
    _, _, real_out = invoke(capsys, "--human", "--workspace", str(tmp_path), "doctor")

    other = tmp_path / "other"
    other.mkdir()
    _lying_declared_env(other)
    monkeypatch.setattr("aef._version.__version__", "1.2.0")
    _, _, fake_out = invoke(capsys, "--human", "--workspace", str(other), "doctor")

    assert "Evidence  : none observed (tree-only read)" in fake_out.out
    assert "Evidence  : dist-info-metadata-wheel" in real_out.out
    assert fake_out.out != real_out.out


def test_pass_human_shows_offline_basis_when_present(tmp_path, capsys, monkeypatch):
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    _write_jsonschema_wheel(tmp_path)
    monkeypatch.setattr("aef.runtime_discovery.module_importable", lambda: True)
    code, _, captured = invoke(capsys, "--human", "--workspace", str(tmp_path), "doctor")
    assert code == 0
    assert "self_attested_checksum" in captured.out
    assert "checksum_matched" in captured.out


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


def test_empty_dist_info_files_do_not_count_as_install_evidence(tmp_path):
    env = write_venv(tmp_path / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    pkg = _pkg_root(env)
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "_version.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    dist = pkg.parent / "agent_evolution_framework-1.2.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_bytes(b"")
    (dist / "WHEEL").write_bytes(b"")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["declared_env_install_evidence"] == []


def test_duplicate_expected_version_keys_are_invalid(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text(
        '{"expected_package_version":"1.2.0","expected_package_version":"9.9.9"}',
        encoding="utf-8",
    )
    info = read_expected_package_version(tmp_path)
    assert info["status"] == "invalid"


def test_invalid_expected_preserves_declared_provenance(tmp_path, capsys):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text("{not-json", encoding="utf-8")
    env = write_venv(tmp_path / ".aef-venv", kind="windows" if os.name == "nt" else "posix")
    _write_declared_aef(env, "9.9.9")
    _, _, captured = invoke(capsys, "--human", "--workspace", str(tmp_path), "doctor")
    assert "invalid_expected_package_version" in captured.out
    assert "Method    : declared_env" in captured.out
    assert "Source    :" in captured.out


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
