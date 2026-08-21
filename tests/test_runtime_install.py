from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from aef.runtime_install import InstallRefused, install_isolated, verify_installed
from tests.test_runtime_discovery import no_path, write_venv


def hooks():
    return {"path_lookup": no_path, "can_import": lambda: False}


def fake_runner(commands=None):
    seen = commands if commands is not None else []

    def run(command, **_kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="1.2.0\n", stderr="")

    return run, seen


def test_refuses_without_consent(tmp_path):
    with pytest.raises(InstallRefused):
        install_isolated(tmp_path, consented=False, **hooks())
    assert not (tmp_path / ".aef-venv").exists()
    assert list(tmp_path.iterdir()) == []


def test_hash_mismatch_blocks_before_any_write(tmp_path, monkeypatch):
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(b"not-a-wheel")
    (tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl.sha256").write_text(
        "0" * 64 + "\n",
        encoding="utf-8",
    )

    def fail_create(*_args, **_kwargs):
        raise AssertionError("hash mismatch must not create a venv")

    monkeypatch.setattr("aef.runtime_install.venv.create", fail_create)
    with pytest.raises(InstallRefused, match="hash"):
        install_isolated(tmp_path, consented=True, runner=fake_runner()[0], **hooks())
    assert not (tmp_path / ".aef-venv").exists()
    assert not (tmp_path / ".agent").exists()


def test_hash_ok_installs_offline_without_touching_existing_venv(tmp_path, monkeypatch):
    existing = write_venv(tmp_path / ".venv", kind="windows")
    keep = (existing / "pyvenv.cfg").read_bytes()
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    payload = b"wheel-bytes"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl.sha256").write_text(
        f"{digest}  {wheel.name}\n",
        encoding="utf-8",
    )
    created = []

    def fake_create(path, **_kwargs):
        created.append(Path(path))
        Path(path).mkdir()
        scripts = Path(path) / "Scripts"
        posix = Path(path) / "bin"
        scripts.mkdir()
        posix.mkdir()
        (scripts / "python.exe").write_bytes(b"")
        (posix / "python").write_bytes(b"")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    runner, seen = fake_runner()
    result = install_isolated(tmp_path, consented=True, runner=runner, **hooks())
    assert result["changed"] is True
    assert created
    assert "--no-index" in seen[0]
    assert str(existing) not in result["env_path"]
    assert (existing / "pyvenv.cfg").read_bytes() == keep
    assert not (tmp_path / ".agent").exists()


def test_existing_valid_runtime_is_idempotent(tmp_path):
    runner, seen = fake_runner()
    result = install_isolated(
        tmp_path,
        consented=True,
        runner=runner,
        path_lookup=no_path,
        can_import=lambda: True,
    )
    assert result["changed"] is False
    assert result["reason"] == "runtime_already_valid"
    assert seen == []
    assert not (tmp_path / ".aef-venv").exists()


def test_verify_skips_audit_when_workspace_is_missing(tmp_path):
    runner, seen = fake_runner()
    report = verify_installed(tmp_path / "python", tmp_path, runner=runner)
    assert report["version_ok"] is True
    assert report["audit_ran"] is False
    assert len(seen) == 1
    assert seen[0][1:] == ["-m", "aef", "--version"]


def test_verify_runs_audit_only_when_manifest_exists(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "manifest.json").write_text("{}", encoding="utf-8")
    runner, seen = fake_runner()
    report = verify_installed(tmp_path / "python", tmp_path, runner=runner)
    assert report["audit_ran"] is True
    assert seen[1][-1] == "audit"


def test_cli_install_is_idempotent_when_runtime_is_valid(tmp_path, capsys, monkeypatch):
    from aef import cli

    def fail_create(*_args, **_kwargs):
        raise AssertionError("valid runtime must not create a venv")

    monkeypatch.setattr("aef.runtime_install.venv.create", fail_create)
    code = cli.main(["--json", "--workspace", str(tmp_path), "doctor", "--install"])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert code == 0
    assert envelope["status"] == "NO_CHANGE"
    assert envelope["command"] == "DOCTOR"
    assert not (tmp_path / ".aef-venv").exists()
