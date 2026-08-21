"""Lot 2 blockers B1/B2/B3 — must fail together on 55cc832 before any fix."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_doctor import diagnose_runtime, proposed_install_command
from aef.runtime_install import InstallRefused, install_isolated
from aef.upgrade_compat import installed_package_version
from tests.test_runtime_discovery import no_path, write_venv


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


def extract_pin(command) -> str | None:
    """Return agent-evolution-framework==VERSION from a command list or string."""
    if isinstance(command, str):
        parts = command.replace('"', "").split()
    else:
        parts = [str(part) for part in command]
    for part in parts:
        if part.startswith("agent-evolution-framework=="):
            return part.split("==", 1)[1]
    return None


@pytest.mark.parametrize(
    "scenario",
    [
        "absent",
        "equal",
        "divergent",
        "local_wheel",
    ],
)
def test_b1_proposed_and_executed_install_spec_share_one_pin(tmp_path, monkeypatch, scenario):
    """B1: proposed install pin must equal executed pip pin (single resolver)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    installed = installed_package_version()
    wheel = None
    if scenario == "absent":
        write_expected(workspace, None)
        expected = None
    elif scenario == "equal":
        write_expected(workspace, installed)
        expected = installed
    elif scenario == "divergent":
        write_expected(workspace, "9.9.9")
        expected = "9.9.9"
    else:
        write_expected(workspace, None)
        expected = None
        payload = b"wheel-bytes-lot2"
        wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
        wheel.write_bytes(payload)
        import hashlib
        digest = hashlib.sha256(payload).hexdigest()
        Path(str(wheel) + ".sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")

    diagnosis = diagnose_runtime(
        workspace, path_lookup=no_path, can_import=lambda: False,
    )
    proposed = diagnosis["install_command"]
    assert proposed

    created = []
    seen = []

    def fake_create(path, **_kwargs):
        created.append(Path(path))
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        scripts = root / "Scripts"
        posix = root / "bin"
        scripts.mkdir(exist_ok=True)
        posix.mkdir(exist_ok=True)
        (scripts / "python.exe").write_bytes(b"")
        (posix / "python").write_bytes(b"")
        (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    def runner(command, **_kwargs):
        seen.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    result = install_isolated(
        workspace,
        consented=True,
        runner=runner,
        path_lookup=no_path,
        can_import=lambda: False,
    )
    assert result["changed"] is True
    pip_commands = [cmd for cmd in seen if len(cmd) >= 3 and cmd[1:3] == ["-m", "pip"]]
    assert pip_commands, f"no pip command executed; seen={seen}"
    executed = pip_commands[0]

    if scenario == "local_wheel":
        assert "--no-index" in proposed
        assert "--no-index" in executed
        assert wheel is not None
        assert wheel.name in proposed or str(wheel) in " ".join(str(p) for p in executed)
    else:
        proposed_pin = extract_pin(proposed)
        executed_pin = extract_pin(executed)
        assert proposed_pin is not None
        assert executed_pin is not None
        assert proposed_pin == executed_pin
        if expected is None:
            assert proposed_pin == installed
        else:
            assert proposed_pin == expected


def test_b2_install_does_not_force_ok_when_expected_unmet(tmp_path, capsys, monkeypatch):
    """B2: expected 9.9.9 + installed 1.2.0 must not yield ok:true / exit 0."""
    write_expected(tmp_path, "9.9.9")
    created = []

    def fake_create(path, **_kwargs):
        created.append(Path(path))
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        scripts = root / "Scripts"
        posix = root / "bin"
        scripts.mkdir(exist_ok=True)
        posix.mkdir(exist_ok=True)
        (scripts / "python.exe").write_bytes(b"")
        (posix / "python").write_bytes(b"")
        (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    def runner(command, **_kwargs):
        if command[-2:] == ["--version"] or (len(command) >= 3 and command[-1] == "--version"):
            return subprocess.CompletedProcess(
                command, 0, stdout=f"aef {installed_package_version()}\n", stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    monkeypatch.setattr("aef.runtime_install.subprocess.run", runner)
    monkeypatch.setattr("aef.cli.install_isolated", lambda *a, **k: install_isolated(
        tmp_path, consented=True, runner=runner, path_lookup=no_path, can_import=lambda: False,
    ))

    monkeypatch.setattr(
        "aef.runtime_install.diagnose_runtime",
        lambda workspace, **hooks: diagnose_runtime(
            workspace, path_lookup=no_path, can_import=lambda: False,
        ),
    )
    monkeypatch.setattr(
        "aef.cli.diagnose_runtime",
        lambda workspace, **hooks: diagnose_runtime(
            workspace, path_lookup=no_path, can_import=lambda: False,
        ),
    )
    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor", "--install",
    )
    assert code != 0
    assert envelope.get("ok") is False
    result = envelope.get("result") or {}
    if "decision" in result:
        assert result["decision"] != "OK"
    assert result.get("expected_package_version", "9.9.9") == "9.9.9"


def test_b3_preexisting_venv_python_is_never_executed(tmp_path, monkeypatch):
    """B3: a planted .aef-venv/bin/python witness must never run."""
    kind = "windows" if os.name == "nt" else "posix"
    env = write_venv(tmp_path / ".aef-venv", kind=kind)
    witness = tmp_path / "WITNESS_EXECUTED"
    if kind == "windows":
        planted = env / "Scripts" / "python.exe"
    else:
        planted = env / "bin" / "python"
    # Replace with a marker-writing script is platform-specific; instead fail if spawned.
    planted.write_bytes(b"must-not-run-lot2-b3")

    def fail_runner(command, **_kwargs):
        for part in command:
            try:
                Path(str(part)).resolve().relative_to(env.resolve())
            except (OSError, ValueError):
                continue
            witness.write_text("executed", encoding="utf-8")
            raise AssertionError(f"pre-existing venv binary must not be spawned: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    created = []

    def fake_create(path, **_kwargs):
        created.append(Path(path))
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        scripts = root / "Scripts"
        posix = root / "bin"
        scripts.mkdir(exist_ok=True)
        posix.mkdir(exist_ok=True)
        (scripts / "python.exe").write_bytes(b"fresh")
        (posix / "python").write_bytes(b"fresh")
        (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    monkeypatch.setattr("aef.runtime_install.venv.create", fake_create)
    # Real install may refuse or create a distinct env; either way witness stays untouched.
    try:
        install_isolated(
            tmp_path,
            consented=True,
            runner=fail_runner,
            path_lookup=no_path,
            can_import=lambda: False,
        )
    except (InstallRefused, AssertionError):
        pass
    assert not witness.exists()
    assert planted.read_bytes() == b"must-not-run-lot2-b3"
