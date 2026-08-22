"""Lot 2 majors — read-only doctor coverage retained after install removal."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import DECISION_BLOCKED, discover_runtime, is_pep440_version_token, read_expected_package_version
from aef.runtime_doctor import classify_local_artifact, diagnose_runtime, resolve_package_install_spec
from tests.test_runtime_discovery import no_path


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


def test_m1_path_method_no_longer_probes_or_passes(tmp_path):
    discovered = discover_runtime(tmp_path, can_import=lambda: False)
    assert discovered["discovery_method"] != "path"
    assert is_pep440_version_token("garbage") is False
    assert is_pep440_version_token("9.9.9") is True
    assert is_pep440_version_token("1.2.0") is True

    module_hit = discover_runtime(tmp_path, can_import=lambda: True)
    assert module_hit["discovery_method"] == "python_module"


def test_m2_pypi_spec_pins_index(tmp_path):
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
    diagnosis = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert "https://pypi.org/simple" in diagnosis["install_command"]


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
    diagnosis = diagnose_runtime(tmp_path, can_import=lambda: True)
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
    diagnosis = diagnose_runtime(tmp_path, can_import=lambda: True)
    assert diagnosis["decision"] == DECISION_BLOCKED
    assert diagnosis["blocked_cause"] == "invalid_expected_package_version"


def test_m5_offline_basis_requires_jsonschema_wheel(tmp_path):
    payload = b"wheel-bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    without_dep = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert without_dep["local_artifact"] == "checksum_matched"
    assert without_dep["network_required"] is True
    assert without_dep["offline_basis"] is None
    assert "--index-url" in without_dep["install_command"]
    assert "--no-index" not in without_dep["install_command"]

    dep = tmp_path / "jsonschema-4.22.0-py3-none-any.whl"
    with zipfile.ZipFile(dep, "w") as archive:
        archive.writestr("dummy.txt", "dep")
    with_dep = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert with_dep["network_required"] is False
    assert with_dep["offline_basis"] == "self_attested_checksum"
    assert "--no-index" in with_dep["install_command"]


def test_m7_ambiguous_wheels_block_when_install_required(tmp_path):
    (tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl").write_bytes(b"a")
    (tmp_path / "agent_evolution_framework-1.3.0-py3-none-any.whl").write_bytes(b"b")
    artifact, wheel, candidates = classify_local_artifact(tmp_path)
    assert artifact == "ambiguous"
    assert wheel is None
    assert len(candidates) == 2
    diagnosis = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert diagnosis["decision"] == DECISION_BLOCKED
    assert diagnosis["blocked_cause"] == "ambiguous_local_wheels"
    assert diagnosis["install_command"] == ""

    write_expected(tmp_path, "1.2.0")
    selected, chosen, _ = classify_local_artifact(tmp_path, expected_version="1.2.0")
    assert selected == "available_unverified"
    assert chosen is not None
    assert "1.2.0" in chosen.name


def test_m8_checksum_matched_versus_available_unverified(tmp_path):
    payload = b"bytes"
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(payload)
    artifact, _, _ = classify_local_artifact(tmp_path)
    assert artifact == "available_unverified"
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / f"{wheel.name}.sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    artifact, _, _ = classify_local_artifact(tmp_path)
    assert artifact == "checksum_matched"


def test_available_unverified_proposes_pypi_not_no_index(tmp_path):
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    result = diagnose_runtime(tmp_path, can_import=lambda: False)
    assert result["local_artifact"] == "available_unverified"
    assert result["network_required"] is True
    assert "--index-url" in result["install_command"]
    assert "--no-index" not in result["install_command"]


def test_blocked_envelope_omits_install_command(tmp_path, capsys):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "runtime-requirements.json").write_text("{not-json", encoding="utf-8")
    code, envelope, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "doctor")
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["result"]["install_command"] == ""
    assert envelope["meta"]["blocked_cause"] == "invalid_expected_package_version"
    assert envelope["meta"].get("blocked_path")
