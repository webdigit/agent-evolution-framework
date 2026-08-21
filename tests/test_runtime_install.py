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


def make_venv_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Scripts").mkdir(exist_ok=True)
    (root / "bin").mkdir(exist_ok=True)
    (root / "Scripts" / "python.exe").write_bytes(b"")
    (root / "bin" / "python").write_bytes(b"")
    (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")


def fake_runner(commands=None, *, version_stdout: str = "aef 1.2.0\n"):
    seen = commands if commands is not None else []

    def run(command, **_kwargs):
        seen.append(list(command))
        cmd = [str(p) for p in command]
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            make_venv_tree(Path(cmd[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "--version" in cmd or (cmd and cmd[-1] == "--version"):
            return subprocess.CompletedProcess(command, 0, stdout=version_stdout, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    return run, seen


def test_refuses_without_consent(tmp_path):
    with pytest.raises(InstallRefused):
        install_isolated(tmp_path, consented=False, **hooks())
    assert not (tmp_path / ".aef-venv").exists()
    assert list(tmp_path.iterdir()) == []


def test_hash_mismatch_blocks_before_any_write(tmp_path):
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    wheel.write_bytes(b"not-a-wheel")
    (tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl.sha256").write_text(
        "0" * 64 + "\n",
        encoding="utf-8",
    )

    def fail_runner(command, **_kwargs):
        raise AssertionError(f"hash mismatch must not spawn: {command}")

    with pytest.raises(InstallRefused, match="hash"):
        install_isolated(tmp_path, consented=True, runner=fail_runner, **hooks())
    assert not (tmp_path / ".aef-venv").exists()
    assert not (tmp_path / ".agent").exists()


def test_hash_ok_installs_offline_without_touching_existing_venv(tmp_path):
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
    (tmp_path / "jsonschema-4.22.0-py3-none-any.whl").write_bytes(b"dep")
    runner, seen = fake_runner()
    result = install_isolated(tmp_path, consented=True, runner=runner, **hooks())
    assert result["changed"] is True
    pip_cmds = [cmd for cmd in seen if "-m" in cmd and "pip" in cmd]
    assert pip_cmds
    assert "--no-index" in pip_cmds[0]
    assert "-I" in pip_cmds[0]
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
    assert seen[0][1:] == ["-I", "-m", "aef", "--version"]


def test_verify_runs_audit_only_when_manifest_exists(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "manifest.json").write_text("{}", encoding="utf-8")
    runner, seen = fake_runner()
    report = verify_installed(tmp_path / "python", tmp_path, runner=runner)
    assert report["audit_ran"] is True
    assert seen[1][-1] == "audit"
    assert "-I" in seen[1]


def test_cli_install_is_idempotent_when_runtime_is_valid(tmp_path, capsys, monkeypatch):
    from aef import cli

    def fail_runner(command, **_kwargs):
        raise AssertionError(f"valid runtime must not spawn: {command}")

    monkeypatch.setattr("aef.runtime_install.subprocess.run", fail_runner)
    code = cli.main(["--json", "--workspace", str(tmp_path), "doctor", "--install"])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert code == 0
    assert envelope["status"] == "NO_CHANGE"
    assert envelope["command"] == "DOCTOR"
    assert not (tmp_path / ".aef-venv").exists()
