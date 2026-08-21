"""Lot 2 majors M1–M8 and related minors for Runtime Doctor."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import (
    DECISION_BLOCKED,
    discover_runtime,
    parse_aef_version_output,
    probe_path_package_version,
    read_expected_package_version,
)
from aef.runtime_doctor import classify_local_artifact, diagnose_runtime, resolve_package_install_spec
from aef.runtime_install import InstallRefused, install_isolated, verify_installed
from tests.test_runtime_discovery import no_path, write_venv


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def write_expected(workspace: Path, version: str) -> None:
    agent = workspace / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": version}),
        encoding="utf-8",
    )


def test_m1_path_method_requires_observed_version(tmp_path, monkeypatch):
    binary = tmp_path / ("aef.exe" if os.name == "nt" else "aef")
    binary.write_bytes(b"")
    monkeypatch.setattr(
        "aef.runtime_discovery.path_binary_compatible",
        lambda path: True,
    )
    calls = []

    def runner(command, **kwargs):
        calls.append(list(command))
        assert kwargs.get("timeout") is not None
        return subprocess.CompletedProcess(command, 0, stdout="aef 1.2.0\n", stderr="")

    discovered = discover_runtime(
        tmp_path,
        path_lookup=lambda _name: str(binary),
        can_import=lambda: False,
        path_version_runner=runner,
    )
    assert discovered["discovery_method"] == "path"
    assert discovered["found_package_version"] == "1.2.0"
    assert calls and calls[0][-1] == "--version"

    def bad_runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="not-a-version\n", stderr="")

    fallback = discover_runtime(
        tmp_path,
        path_lookup=lambda _name: str(binary),
        can_import=lambda: True,
        path_version_runner=bad_runner,
    )
    assert fallback["discovery_method"] == "python_module"
    assert parse_aef_version_output("garbage") is None


def test_m2_pypi_spec_pins_index_and_strips_pip_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://evil.example/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://evil.example/extra")
    spec = resolve_package_install_spec(
        expected_package_version="1.2.0",
        artifact="absent",
        wheel=None,
    )
    assert spec.mode == "pypi"
    assert "--index-url" in spec.pip_args
    assert "https://pypi.org/simple" in spec.pip_args
    assert "--isolated" in spec.pip_args
    assert "--no-cache-dir" in spec.pip_args
    diagnosis = diagnose_runtime(tmp_path, path_lookup=no_path, can_import=lambda: False)
    assert "https://pypi.org/simple" in diagnosis["install_command"]

    seen_env = []

    def runner(command, **kwargs):
        seen_env.append(kwargs.get("env"))
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    def fake_create(path, **_kwargs):
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "Scripts").mkdir(exist_ok=True)
        (root / "bin").mkdir(exist_ok=True)
        (root / "Scripts" / "python.exe").write_bytes(b"")
        (root / "bin" / "python").write_bytes(b"")
        (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    install_isolated(
        tmp_path, consented=True, runner=runner, path_lookup=no_path, can_import=lambda: False,
    )
    assert seen_env and seen_env[0] is not None
    assert "PIP_INDEX_URL" not in seen_env[0]
    assert "PIP_EXTRA_INDEX_URL" not in seen_env[0]


@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda path: path.write_text('{"expected_package_version": "1.2.0"', encoding="utf-8"),
        lambda path: path.write_text(
            json.dumps({"expected_package_version": 1.2}), encoding="utf-8",
        ),
        lambda path: path.write_text(
            json.dumps({"expected_pakage_version": "1.2.0"}), encoding="utf-8",
        ),
    ],
    ids=["truncated_json", "numeric_value", "typo_key"],
)
def test_m3_invalid_expected_version_is_blocked(tmp_path, payload_factory):
    agent = tmp_path / ".agent"
    agent.mkdir()
    requirements = agent / "runtime-requirements.json"
    payload_factory(requirements)
    info = read_expected_package_version(tmp_path)
    assert info["status"] == "invalid"
    assert info["path"] == ".agent/runtime-requirements.json"
    diagnosis = diagnose_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: True,
    )
    assert diagnosis["decision"] == DECISION_BLOCKED
    assert diagnosis["blocked_cause"] == "invalid_expected_package_version"
    assert "runtime-requirements.json" in (diagnosis["blocked_path"] or "")
    assert diagnosis["install_command"] == ""


def test_m3_unreadable_expected_version_is_blocked(tmp_path, monkeypatch):
    agent = tmp_path / ".agent"
    agent.mkdir()
    requirements = agent / "runtime-requirements.json"
    requirements.write_text(
        json.dumps({"expected_package_version": "1.2.0"}),
        encoding="utf-8",
    )
    original = Path.read_text

    def boom(self, *args, **kwargs):
        if self == requirements:
            raise OSError("permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    info = read_expected_package_version(tmp_path)
    assert info["status"] == "invalid"
    diagnosis = diagnose_runtime(
        tmp_path, path_lookup=no_path, can_import=lambda: True,
    )
    assert diagnosis["decision"] == DECISION_BLOCKED
    assert diagnosis["blocked_cause"] == "invalid_expected_package_version"


def test_m4_valid_venv_unmet_expected_is_not_exit_zero(tmp_path, capsys, monkeypatch):
    kind = "windows" if os.name == "nt" else "posix"
    env = write_venv(tmp_path / ".aef-venv", kind=kind)
    pkg = env / ("Lib" if kind == "windows" else "lib/python3.11") / "site-packages" / "aef"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "_version.py").write_text('__version__ = "1.2.0"\n', encoding="utf-8")
    write_expected(tmp_path, "9.9.9")

    def fake_create(path, **_kwargs):
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "Scripts").mkdir(exist_ok=True)
        (root / "bin").mkdir(exist_ok=True)
        (root / "Scripts" / "python.exe").write_bytes(b"")
        (root / "bin" / "python").write_bytes(b"")
        (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="aef 1.2.0\n", stderr="")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    monkeypatch.setattr("aef.runtime_install.subprocess.run", runner)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor", "--install",
    )
    assert code != 0
    assert envelope["ok"] is False
    decision = (envelope.get("result") or {}).get("decision")
    assert decision != "OK"


def test_m5_network_required_false_needs_jsonschema_wheel(tmp_path):
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    without_dep = diagnose_runtime(tmp_path, path_lookup=no_path, can_import=lambda: False)
    assert without_dep["local_artifact"] == "verified"
    assert without_dep["network_required"] is True

    (tmp_path / "jsonschema-4.22.0-py3-none-any.whl").write_bytes(b"dep")
    with_dep = diagnose_runtime(tmp_path, path_lookup=no_path, can_import=lambda: False)
    assert with_dep["network_required"] is False


def test_m6_timeouts_become_distinct_install_refused(tmp_path, monkeypatch):
    def timeout_runner(_command, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="pip", timeout=1)

    def fake_create(path, **_kwargs):
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        (root / "Scripts").mkdir(exist_ok=True)
        (root / "bin").mkdir(exist_ok=True)
        (root / "Scripts" / "python.exe").write_bytes(b"")
        (root / "bin" / "python").write_bytes(b"")
        (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    with pytest.raises(InstallRefused) as pip_timeout:
        install_isolated(
            tmp_path,
            consented=True,
            runner=timeout_runner,
            path_lookup=no_path,
            can_import=lambda: False,
        )
    assert pip_timeout.value.reason == "pip_timeout"

    with pytest.raises(InstallRefused) as verify_timeout:
        verify_installed(tmp_path / "python", tmp_path, runner=timeout_runner)
    assert verify_timeout.value.reason == "verify_timeout"


def test_m7_ambiguous_wheels_block_instead_of_first_alpha(tmp_path):
    (tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl").write_bytes(b"a")
    (tmp_path / "agent_evolution_framework-1.3.0-py3-none-any.whl").write_bytes(b"b")
    artifact, wheel, candidates = classify_local_artifact(tmp_path)
    assert artifact == "ambiguous"
    assert wheel is None
    assert len(candidates) == 2
    diagnosis = diagnose_runtime(tmp_path, path_lookup=no_path, can_import=lambda: False)
    assert diagnosis["decision"] == DECISION_BLOCKED
    assert diagnosis["blocked_cause"] == "ambiguous_local_wheels"
    assert diagnosis["install_command"] == ""

    write_expected(tmp_path, "1.2.0")
    selected, chosen, _ = classify_local_artifact(tmp_path, expected_version="1.2.0")
    assert selected == "available_unverified"
    assert chosen is not None
    assert "1.2.0" in chosen.name


def test_m8_verified_versus_available_unverified(tmp_path):
    payload = b"bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    artifact, _, _ = classify_local_artifact(tmp_path)
    assert artifact == "available_unverified"
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    artifact, _, _ = classify_local_artifact(tmp_path)
    assert artifact == "verified"


def test_blocked_envelope_omits_install_command(tmp_path, capsys):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text("{not-json", encoding="utf-8")
    code, envelope, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "doctor")
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["result"]["install_command"] == ""
    assert envelope["meta"]["blocked_cause"] == "invalid_expected_package_version"


def test_probe_path_helper_parses_strictly(tmp_path):
    binary = tmp_path / "aef"
    binary.write_text("", encoding="utf-8")

    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="aef 2.0.0\n", stderr="")

    assert probe_path_package_version(binary, runner=runner) == "2.0.0"
