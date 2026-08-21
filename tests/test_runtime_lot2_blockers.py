"""Lot 2 blockers + Lot 2 bis N2/B1/N3/N4/N5.

Hook review (can_import=False): see
`_bmad-output/implementation-artifacts/LOT2BIS-hook-review.md`.

Occurrences historically injected can_import=False to reach declared_env/none —
states the CLI cannot produce (module_importable is always true in-process).
Critical N2/B1/N3/N5 CLI properties below call cli.main without that hook.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import parse_aef_version_output
from aef.runtime_doctor import diagnose_runtime
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
        parts = command.replace('"', "").replace("'", "").split()
    else:
        parts = [str(part) for part in command]
    for part in parts:
        if part.startswith("agent-evolution-framework=="):
            return part.split("==", 1)[1]
    return None


def extract_wheel_path(command) -> str | None:
    if isinstance(command, str):
        parts = command.replace('"', "").replace("'", "").split()
    else:
        parts = [str(part) for part in command]
    for part in parts:
        if part.endswith(".whl"):
            return part
    return None


def extract_index_axis(command) -> tuple[str, str | None]:
    """Return ('no-index', find_links) or ('index-url', url)."""
    if isinstance(command, str):
        parts = command.replace('"', "").replace("'", "").split()
    else:
        parts = [str(part) for part in command]
    if "--no-index" in parts:
        find_links = None
        if "--find-links" in parts:
            find_links = parts[parts.index("--find-links") + 1]
        return ("no-index", find_links)
    if "--index-url" in parts:
        return ("index-url", parts[parts.index("--index-url") + 1])
    raise AssertionError(f"no index axis in {command!r}")


def extract_env_name_from_proposed(proposed: str) -> str:
    parts = proposed.replace('"', "").replace("'", "").split()
    assert "-m" in parts and "venv" in parts
    idx = parts.index("venv")
    return Path(parts[idx + 1]).name


def make_venv_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Scripts").mkdir(exist_ok=True)
    (root / "bin").mkdir(exist_ok=True)
    (root / "Scripts" / "python.exe").write_bytes(b"")
    (root / "bin" / "python").write_bytes(b"")
    (root / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")


def install_runner(seen: list, *, pip_rc: int = 0, pip_stderr: str = ""):
    """Production-shaped runner: handles `python -m venv` and `python -I -m pip`."""

    def runner(command, **_kwargs):
        seen.append(list(command))
        cmd = [str(part) for part in command]
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            env_path = Path(cmd[-1])
            make_venv_tree(env_path)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "-m" in cmd and "pip" in cmd:
            return subprocess.CompletedProcess(
                command, pip_rc, stdout="ok\n", stderr=pip_stderr,
            )
        if "--version" in cmd or (cmd and cmd[-1] == "--version"):
            return subprocess.CompletedProcess(
                command, 0, stdout=f"aef {installed_package_version()}\n", stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    return runner


# --- B1: strict propose/execute equality (three axes) ---


@pytest.mark.parametrize(
    "scenario",
    [
        "absent",
        "equal",
        "divergent",
        "local_wheel",
        "preexisting_aef_venv",
    ],
)
def test_b1_proposed_and_executed_share_three_axes(tmp_path, monkeypatch, scenario):
    """B1: proposed and executed must match on pin/wheel, index flags, and env name."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    installed = installed_package_version()
    wheel = None

    if scenario == "absent":
        write_expected(workspace, None)
        expected_pin = installed
    elif scenario == "equal":
        write_expected(workspace, installed)
        expected_pin = installed
    elif scenario == "divergent":
        write_expected(workspace, "9.9.9")
        expected_pin = "9.9.9"
    elif scenario == "local_wheel":
        write_expected(workspace, None)
        expected_pin = installed
        payload = b"wheel-bytes-lot2"
        wheel = workspace / "agent_evolution_framework-1.2.0-py3-none-any.whl"
        wheel.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        Path(str(wheel) + ".sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
        (workspace / "jsonschema-4.22.0-py3-none-any.whl").write_bytes(b"dep")
    else:
        write_expected(workspace, "9.9.9")
        expected_pin = "9.9.9"
        kind = "windows" if os.name == "nt" else "posix"
        write_venv(workspace / ".aef-venv", kind=kind)

    # Library isolation: force INSTALL_REQUIRED so propose+execute both run.
    # Justified: unit-tests the shared resolver contract; CLI N2/N5 cover end paths.
    diagnosis = diagnose_runtime(
        workspace, path_lookup=no_path, can_import=lambda: False,
    )
    proposed = diagnosis["install_command"]
    assert proposed
    proposed_env = extract_env_name_from_proposed(proposed)
    proposed_index = extract_index_axis(proposed)

    seen: list = []
    runner = install_runner(seen)
    result = install_isolated(
        workspace,
        consented=True,
        runner=runner,
        path_lookup=no_path,
        can_import=lambda: False,
    )
    assert result["changed"] is True
    executed_env = Path(result["env_path"]).name
    pip_commands = [
        cmd for cmd in seen
        if "-m" in cmd and "pip" in cmd
    ]
    assert pip_commands, f"no pip command executed; seen={seen}"
    executed = pip_commands[0]
    executed_index = extract_index_axis(executed)

    # Axis 3: target env directory name
    assert proposed_env == executed_env

    # Axis 2: index / offline flags
    assert proposed_index[0] == executed_index[0]
    if proposed_index[0] == "index-url":
        assert proposed_index[1] == executed_index[1] == "https://pypi.org/simple"
    else:
        assert proposed_index[0] == "no-index"
        assert "--find-links" in proposed and "--find-links" in executed

    # Axis 1: version pin or absolute wheel path
    if scenario == "local_wheel":
        assert wheel is not None
        proposed_wheel = extract_wheel_path(proposed)
        executed_wheel = extract_wheel_path(executed)
        assert proposed_wheel is not None and executed_wheel is not None
        assert proposed_wheel == executed_wheel
        assert Path(proposed_wheel).is_absolute()
        assert Path(proposed_wheel).name == wheel.name
        # Basename-only as the install artifact is forbidden (copy-paste must work).
        assert proposed_wheel != wheel.name
        assert executed_wheel == proposed_wheel
    else:
        proposed_pin = extract_pin(proposed)
        executed_pin = extract_pin(executed)
        assert proposed_pin == executed_pin == expected_pin

    if scenario == "preexisting_aef_venv":
        assert proposed_env != ".aef-venv"
        assert executed_env != ".aef-venv"


def test_b1_cli_local_wheel_absolute_path_in_proposal(tmp_path, capsys):
    """CLI-facing: proposal must embed absolute wheel path (copy-paste safe)."""
    write_expected(tmp_path, "9.9.9")
    payload = b"wheel-cli-b1"
    wheel = tmp_path / "agent_evolution_framework-9.9.9-py3-none-any.whl"
    wheel.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    Path(str(wheel) + ".sha256").write_text(f"{digest}  {wheel.name}\n", encoding="utf-8")
    (tmp_path / "jsonschema-4.22.0-py3-none-any.whl").write_bytes(b"dep")

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor",
    )
    assert code == 8
    proposed = envelope["result"]["install_command"]
    abs_wheel = str(wheel.resolve())
    assert abs_wheel in proposed or abs_wheel.replace("\\", "/") in proposed.replace("\\", "/")
    assert "--no-index" in proposed
    # Must not propose basename-only as the artifact token after find-links
    assert f"find-links" in proposed


def test_b2_install_does_not_force_ok_when_expected_unmet(tmp_path, capsys, monkeypatch):
    """B2: expected 9.9.9 + installed 1.2.0 must not yield ok:true / exit 0."""
    write_expected(tmp_path, "9.9.9")
    seen: list = []

    def runner(command, **_kwargs):
        seen.append(list(command))
        cmd = [str(p) for p in command]
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            make_venv_tree(Path(cmd[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "--version" in cmd or (cmd and cmd[-1] == "--version"):
            return subprocess.CompletedProcess(
                command, 0, stdout=f"aef {installed_package_version()}\n", stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("aef.runtime_install.subprocess.run", runner)

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
    """B3: a planted .aef-venv/bin/python witness must never run.

    library_unit_kept_with_justification: isolates install_isolated helper;
    CLI-unreachable can_import=False is intentional so install proceeds.
    """
    kind = "windows" if os.name == "nt" else "posix"
    env = write_venv(tmp_path / ".aef-venv", kind=kind)
    witness = tmp_path / "WITNESS_EXECUTED"
    if kind == "windows":
        planted = env / "Scripts" / "python.exe"
    else:
        planted = env / "bin" / "python"
    planted.write_bytes(b"must-not-run-lot2-b3")

    def fail_runner(command, **_kwargs):
        for part in command:
            try:
                Path(str(part)).resolve().relative_to(env.resolve())
            except (OSError, ValueError):
                continue
            witness.write_text("executed", encoding="utf-8")
            raise AssertionError(f"pre-existing venv binary must not be spawned: {command}")
        cmd = [str(p) for p in command]
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            make_venv_tree(Path(cmd[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

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


# --- N2: doctor must not execute PATH binaries ---


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
    """CLI: PATH witness named aef must not run; must not PASS via path."""
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


def test_n2_coherent_liar_does_not_yield_install_no_change(tmp_path, capsys, monkeypatch):
    """CLI: echo 'aef 9.9.9' on PATH must not make --install return NO_CHANGE."""
    write_expected(tmp_path, "9.9.9")
    bin_dir = tmp_path / "hostile-bin"
    witness = tmp_path / "WITNESS_LIAR"
    _write_path_witness(bin_dir, witness, version_line="aef 9.9.9")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    seen: list = []

    def runner(command, **_kwargs):
        seen.append(list(command))
        cmd = [str(p) for p in command]
        # Fail loudly if PATH witness binary is spawned
        for part in cmd:
            if Path(str(part)).name in {"aef", "aef.cmd", "aef.exe", "aef.bat"}:
                if "hostile-bin" in str(part).replace("\\", "/"):
                    witness.write_text("executed", encoding="utf-8")
                    raise AssertionError(f"PATH liar must not run: {command}")
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            make_venv_tree(Path(cmd[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-m" in cmd and "pip" in cmd:
            return subprocess.CompletedProcess(command, 0, "ok\n", "")
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"aef {installed_package_version()}\n", stderr="",
            )
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr("aef.runtime_install.subprocess.run", runner)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor", "--install",
    )
    assert not witness.exists()
    assert envelope.get("status") != "NO_CHANGE"
    assert not (envelope.get("ok") is True and envelope.get("meta", {}).get("install") == "runtime_already_valid")
    assert code != 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("aef 1.2.0", "1.2.0"),
        ("9.9.9", None),
        ("AEF 9.9.9", None),
        ("Aef 1.2.0", None),
        ("aef 1.2.0\nextra", None),
    ],
)
def test_n2_parse_aef_version_output_strict_prefix(text, expected):
    assert parse_aef_version_output(text) == expected


# --- N3: PYTHONPATH / evil pip ---


def test_n3_evil_pip_on_pythonpath_not_used_during_install(tmp_path, capsys, monkeypatch):
    """CLI: evil pip on PYTHONPATH must not run during install/verify."""
    write_expected(tmp_path, "9.9.9")
    evil = tmp_path / "evil"
    (evil / "pip").mkdir(parents=True)
    sentinel = tmp_path / "SENTINEL_PIP"
    (evil / "pip" / "__init__.py").write_text("", encoding="utf-8")
    (evil / "pip" / "__main__.py").write_text(
        f"from pathlib import Path\n"
        f"Path(r'{sentinel}').write_text('EVIL PIP RAN', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(evil))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "bogus-home"))

    seen_envs: list = []
    seen_cmds: list = []

    def runner(command, **kwargs):
        seen_cmds.append(list(command))
        seen_envs.append(kwargs.get("env"))
        cmd = [str(p) for p in command]
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            make_venv_tree(Path(cmd[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-m" in cmd and "pip" in cmd:
            env = kwargs.get("env") or {}
            assert "PYTHONPATH" not in env
            assert "PYTHONHOME" not in env
            assert "-I" in cmd
            return subprocess.CompletedProcess(command, 0, "ok\n", "")
        if "--version" in cmd or (cmd and cmd[-1] == "--version"):
            env = kwargs.get("env")
            if env is not None:
                assert "PYTHONPATH" not in env
            assert "-I" in cmd
            return subprocess.CompletedProcess(
                command, 0, stdout=f"aef {installed_package_version()}\n", stderr="",
            )
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr("aef.runtime_install.subprocess.run", runner)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor", "--install",
    )
    assert not sentinel.exists()
    assert any("-I" in cmd and "pip" in cmd for cmd in seen_cmds)
    assert code != 0  # version still unmet after mock install
    assert envelope.get("ok") is False


# --- N4: version format + injection ---


def test_n4_injection_payload_rejected_like_m3(tmp_path, capsys):
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


# --- N5: failed install cleanup ---


def test_n5_pip_failure_removes_created_env(tmp_path, capsys, monkeypatch):
    """CLI: pip fail → env directory absent afterwards; no silent leftover."""
    write_expected(tmp_path, "9.9.9")
    seen: list = []

    def runner(command, **_kwargs):
        seen.append(list(command))
        cmd = [str(p) for p in command]
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            make_venv_tree(Path(cmd[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-m" in cmd and "pip" in cmd:
            return subprocess.CompletedProcess(command, 1, "", "pip boom")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr("aef.runtime_install.subprocess.run", runner)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "doctor", "--install",
    )
    assert code == 4
    assert envelope.get("ok") is False
    assert not (tmp_path / ".aef-venv").exists()
    platform_env = tmp_path / f".aef-venv-{'windows' if os.name == 'nt' else 'linux'}"
    # Also no platform leftover from this failure path
    assert not platform_env.exists() or not any(platform_env.iterdir()) if platform_env.exists() else True
    # Prefer: no .aef-venv* left
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.is_dir() and p.name.startswith(".aef-venv")
    ]
    assert leftovers == []
