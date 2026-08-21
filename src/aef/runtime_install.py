"""Isolated AEF install after explicit --install consent. Never mutates .agent/state/."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any, Callable

from .runtime_discovery import host_platform, inspect_venv_tree, workspace_contains
from .runtime_doctor import classify_local_artifact, diagnose_runtime, isolated_env_name
from .upgrade_compat import installed_package_version

Runner = Callable[..., subprocess.CompletedProcess]


class InstallRefused(RuntimeError):
    """Install was not consented or cannot start safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_hash(wheel: Path) -> str | None:
    sibling = Path(str(wheel) + ".sha256")
    sums = wheel.parent / "SHA256SUMS.txt"
    for candidate in (sibling, sums):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].endswith(wheel.name):
                return parts[0].lower()
            if len(parts) == 1 and candidate == sibling:
                return parts[0].lower()
    return None


def target_env_path(workspace: Path, venv_status: str) -> Path:
    return workspace / isolated_env_name(venv_status)


def venv_python(env_path: Path) -> Path:
    if host_platform() == "windows":
        return env_path / "Scripts" / "python.exe"
    posix = env_path / "bin" / "python"
    if posix.is_file():
        return posix
    return env_path / "bin" / "python3"


def _usable_isolated_python(env_path: Path, runner: Runner) -> Path | None:
    if inspect_venv_tree(env_path) != "compatible":
        return None
    python = venv_python(env_path)
    if not python.is_file():
        return None
    completed = runner(
        [str(python), "-m", "aef", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return python
    return None


def _candidate_env_paths(workspace: Path, venv_status: str) -> list[Path]:
    primary = target_env_path(workspace, venv_status)
    paths = [primary]
    platform_named = workspace / isolated_env_name("incompatible")
    if platform_named != primary:
        paths.append(platform_named)
    return paths


def install_isolated(
    workspace: Path,
    *,
    consented: bool,
    runner: Runner = subprocess.run,
    **discovery_hooks: Any,
) -> dict[str, Any]:
    """Create a new isolated env and install AEF. Never rewrite an existing venv."""
    if not consented:
        raise InstallRefused("install requires explicit --install consent")
    diagnosis = diagnose_runtime(workspace, **discovery_hooks)
    if diagnosis["decision"] == "OK":
        return {
            "changed": False,
            "env_path": None,
            "reason": "runtime_already_valid",
            "diagnosis": diagnosis,
        }
    if diagnosis["decision"] == "BLOCKED":
        raise InstallRefused("install is blocked by an external environment path")
    if diagnosis["local_artifact"] == "hash_mismatch":
        raise InstallRefused("local wheel hash does not match the expected digest")
    candidates = _candidate_env_paths(workspace, diagnosis["venv_status"])
    for existing in candidates:
        if existing.exists():
            python = _usable_isolated_python(existing, runner)
            if python is not None:
                return {
                    "changed": False,
                    "env_path": str(existing),
                    "reason": "isolated_env_already_valid",
                    "diagnosis": diagnosis,
                }
    env_path = next((path for path in candidates if not path.exists()), None)
    if env_path is None:
        raise InstallRefused("refusing to reuse an existing environment")
    if env_path.exists() or not workspace_contains(workspace, env_path.parent):
        raise InstallRefused("install target escapes the workspace")
    artifact, wheel = classify_local_artifact(workspace)
    pip_spec: list[str]
    if artifact == "available" and wheel is not None:
        expected = _expected_hash(wheel)
        if expected is not None and _sha256(wheel) != expected:
            raise InstallRefused("local wheel hash does not match the expected digest")
        pip_spec = ["--no-index", "--find-links", str(wheel.parent), str(wheel)]
    else:
        pip_spec = [f"agent-evolution-framework=={installed_package_version()}"]
    venv.create(env_path, with_pip=True, symlinks=os.name != "nt")
    python = venv_python(env_path)
    command = [str(python), "-m", "pip", "install", *pip_spec]
    completed = runner(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise InstallRefused("pip install failed")
    return {
        "changed": True,
        "env_path": str(env_path),
        "python": str(python),
        "diagnosis": diagnosis,
        "created_env": True,
    }


def verify_installed(python: Path, workspace: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    version = runner(
        [str(python), "-m", "aef", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    audit = None
    if (workspace / ".agent" / "manifest.json").is_file():
        audit = runner(
            [str(python), "-m", "aef", "--workspace", str(workspace), "audit"],
            check=False,
            capture_output=True,
            text=True,
        )
    return {
        "version_ok": version.returncode == 0,
        "version_output": (version.stdout or "").strip(),
        "audit_ran": audit is not None,
        "audit_ok": None if audit is None else audit.returncode == 0,
    }


def current_interpreter() -> Path:
    return Path(sys.executable)
