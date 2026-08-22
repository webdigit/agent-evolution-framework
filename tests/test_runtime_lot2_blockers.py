"""Lot 2 bis read-only doctor tests retained after install surface removal."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import is_pep440_version_token


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def write_expected(workspace: Path, version: str | None) -> None:
    agent = workspace / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    path = agent / "runtime-requirements.json"
    if version is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(
        json.dumps({"expected_package_version": version}),
        encoding="utf-8",
    )


def _write_path_witness(bin_dir: Path, witness: Path, *, version_line: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        script = bin_dir / "aef.cmd"
        script.write_text(
            f'@echo off\r\necho PATH-WITNESS-RAN>>"{witness}"\r\necho {version_line}\r\n',
            encoding="utf-8",
        )
        return script
    script = bin_dir / "aef"
    script.write_text(
        "#!/bin/sh\n"
        f'echo PATH-WITNESS-RAN >> "{witness}"\n'
        f'echo "{version_line}"\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_n2_doctor_does_not_execute_path_aef_witness(tmp_path, capsys, monkeypatch):
    write_expected(tmp_path, "9.9.9")
    bin_dir = tmp_path / "hostile-bin"
    witness = tmp_path / "WITNESS_PATH"
    _write_path_witness(bin_dir, witness, version_line="aef 9.9.9")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert not witness.exists(), "doctor must not execute PATH aef"
    assert envelope["result"]["discovery_method"] != "path"
    assert code == 8
    assert envelope["status"] == "INSTALL_REQUIRED"
    assert envelope["ok"] is False


def test_n2_path_symlink_to_shell_is_not_executed(tmp_path, capsys, monkeypatch):
    if os.name == "nt":
        pytest.skip("posix symlink witness")
    write_expected(tmp_path, "9.9.9")
    bin_dir = tmp_path / "hostile-bin"
    bin_dir.mkdir()
    witness = tmp_path / "WITNESS_SYMLINK"
    target = bin_dir / "liar.sh"
    target.write_text(
        "#!/bin/sh\n"
        f'echo SYMLINK-RAN >> "{witness}"\n'
        'echo "aef 9.9.9"\n',
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    link = bin_dir / "aef"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert not witness.exists()
    assert envelope["result"]["discovery_method"] != "path"
    assert code == 8


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.0", True),
        ("1.2", True),
        ("9.9.9", True),
        ("AEF 9.9.9", False),
        ("1.2.0rc1", True),
    ],
)
def test_n2_version_tokens_reject_bare_or_wrong_prefix(text, expected):
    assert is_pep440_version_token(text) is expected


def test_n4_injection_payload_rejected(tmp_path, capsys):
    agent = tmp_path / ".agent"
    agent.mkdir()
    payload = '1.0.0" & echo PWNED & "'
    (agent / "runtime-requirements.json").write_text(
        json.dumps({"expected_package_version": payload}),
        encoding="utf-8",
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["blocked_cause"] == "invalid_expected_package_version"
    assert "PWNED" not in (envelope.get("result") or {}).get("install_command", "")


def test_b1_cli_local_wheel_absolute_path_in_proposal(tmp_path, capsys):
    write_expected(tmp_path, "9.9.9")
    payload = b"wheel-cli-b1"
    wheel = tmp_path / "agent_evolution_framework-9.9.9-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    Path(str(wheel) + ".sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    dep = tmp_path / "jsonschema-4.22.0-py3-none-any.whl"
    with zipfile.ZipFile(dep, "w") as archive:
        archive.writestr("jsonschema-4.22.0.dist-info/METADATA", "Name: jsonschema\n")
        archive.writestr("jsonschema-4.22.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 8
    proposed = envelope["result"]["install_command"]
    abs_wheel = str(wheel.resolve())
    assert abs_wheel in proposed or abs_wheel.replace("\\", "/") in proposed.replace("\\", "/")
    assert "--no-index" in proposed
