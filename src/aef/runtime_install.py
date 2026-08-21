"""Isolated AEF install after explicit --install consent. Never mutates .agent/state/."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any, Callable

from .runtime_discovery import host_platform, inspect_venv_tree, parse_aef_version_output, workspace_contains
from .runtime_doctor import (
    classify_local_artifact,
    diagnose_runtime,
    isolated_env_name,
    resolve_package_install_spec,
)

Runner = Callable[..., subprocess.CompletedProcess]

PROBE_TIMEOUT = 30
PIP_TIMEOUT = 300


class InstallRefused(RuntimeError):
    """Install was not consented or cannot start safely."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        diagnosis: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.diagnosis = diagnosis
        self.detail = detail


def target_env_path(workspace: Path, venv_status: str) -> Path:
    return workspace / isolated_env_name(venv_status)


def venv_python(env_path: Path) -> Path:
    if host_platform() == "windows":
        return env_path / "Scripts" / "python.exe"
    posix = env_path / "bin" / "python"
    if posix.is_file():
        return posix
    return env_path / "bin" / "python3"


def _pip_clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PIP_")}
    return env


def _run(
    runner: Runner,
    command: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict[str, Any] = {
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if env is not None:
        kwargs["env"] = env
    return runner(command, **kwargs)


def _usable_isolated_python(env_path: Path, runner: Runner) -> Path | None:
    if inspect_venv_tree(env_path) != "compatible":
        return None
    python = venv_python(env_path)
    if not python.is_file():
        return None
    try:
        completed = _run(
            runner,
            [str(python), "-m", "aef", "--version"],
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallRefused(
            "timed out probing existing isolated interpreter",
            reason="probe_timeout",
            detail=str(exc),
        ) from exc
    if completed.returncode == 0 and parse_aef_version_output(completed.stdout or ""):
        return python
    return None


def _candidate_env_paths(workspace: Path, venv_status: str) -> list[Path]:
    primary = target_env_path(workspace, venv_status)
    paths = [primary]
    platform_named = workspace / isolated_env_name("incompatible")
    if platform_named != primary:
        paths.append(platform_named)
    return paths


def _fresh_env_path(workspace: Path, venv_status: str) -> Path:
    """Choose a create target that does not reuse a pre-existing interpreter tree."""
    for candidate in _candidate_env_paths(workspace, venv_status):
        if not candidate.exists():
            return candidate
    raise InstallRefused(
        "refusing to reuse an existing environment without --reuse-env",
        reason="reuse_refused",
    )


def install_isolated(
    workspace: Path,
    *,
    consented: bool,
    reuse_env: bool = False,
    runner: Runner = subprocess.run,
    **discovery_hooks: Any,
) -> dict[str, Any]:
    """Create a new isolated env and install AEF. Never rewrite an existing venv."""
    if not consented:
        raise InstallRefused(
            "install requires explicit --install consent",
            reason="consent_required",
        )
    diagnosis = diagnose_runtime(workspace, **discovery_hooks)
    if diagnosis["decision"] == "OK":
        return {
            "changed": False,
            "env_path": None,
            "reason": "runtime_already_valid",
            "diagnosis": diagnosis,
            "install_pin": None,
        }
    if diagnosis["decision"] == "BLOCKED":
        raise InstallRefused(
            "install is blocked",
            reason=diagnosis.get("blocked_cause") or "blocked",
            diagnosis=diagnosis,
        )
    if diagnosis["local_artifact"] == "hash_mismatch":
        raise InstallRefused(
            "local wheel hash does not match the expected digest",
            reason="hash_mismatch",
            diagnosis=diagnosis,
        )
    if diagnosis["local_artifact"] == "ambiguous":
        raise InstallRefused(
            "multiple local wheels; cannot choose install artifact",
            reason="ambiguous_local_wheels",
            diagnosis=diagnosis,
        )

    if reuse_env:
        for existing in _candidate_env_paths(workspace, diagnosis["venv_status"]):
            if not existing.exists():
                continue
            python = _usable_isolated_python(existing, runner)
            if python is not None:
                return {
                    "changed": False,
                    "env_path": str(existing),
                    "reason": "isolated_env_already_valid",
                    "diagnosis": diagnosis,
                    "install_pin": None,
                }
        env_path = _fresh_env_path(workspace, diagnosis["venv_status"])
    else:
        env_path = _fresh_env_path(workspace, diagnosis["venv_status"])

    if not workspace_contains(workspace, env_path):
        raise InstallRefused(
            "install target escapes the workspace",
            reason="path_escape",
            diagnosis=diagnosis,
        )

    artifact, wheel, _candidates = classify_local_artifact(
        workspace,
        expected_version=diagnosis["expected_package_version"],
    )
    if artifact == "hash_mismatch":
        raise InstallRefused(
            "local wheel hash does not match the expected digest",
            reason="hash_mismatch",
            diagnosis=diagnosis,
        )
    spec = resolve_package_install_spec(
        expected_package_version=diagnosis["expected_package_version"],
        artifact=artifact if artifact in {"verified", "available_unverified"} else "absent",
        wheel=wheel if artifact in {"verified", "available_unverified"} else None,
    )

    try:
        venv.create(env_path, with_pip=True, symlinks=os.name != "nt")
    except Exception as exc:  # noqa: BLE001 — surface as refused install
        raise InstallRefused(
            "failed to create isolated virtual environment",
            reason="venv_create_failed",
            diagnosis=diagnosis,
            detail=str(exc),
        ) from exc

    python = venv_python(env_path)
    command = [str(python), "-m", "pip", "install", *spec.pip_args]
    try:
        completed = _run(
            runner,
            command,
            timeout=PIP_TIMEOUT,
            env=_pip_clean_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallRefused(
            "pip install timed out",
            reason="pip_timeout",
            diagnosis=diagnosis,
            detail=str(exc),
        ) from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit {completed.returncode}"
        raise InstallRefused(
            "pip install failed",
            reason="pip_failed",
            diagnosis=diagnosis,
            detail=detail,
        )
    return {
        "changed": True,
        "env_path": str(env_path),
        "python": str(python),
        "diagnosis": diagnosis,
        "created_env": True,
        "install_pin": spec.pin_version,
        "install_mode": spec.mode,
    }


def verify_installed(
    python: Path,
    workspace: Path,
    *,
    expected_version: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    try:
        version = _run(
            runner,
            [str(python), "-m", "aef", "--version"],
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallRefused(
            "version probe timed out",
            reason="verify_timeout",
            detail=str(exc),
        ) from exc
    reported = parse_aef_version_output(version.stdout or "")
    version_ok = (
        version.returncode == 0
        and reported is not None
        and (expected_version is None or reported == expected_version)
    )
    audit = None
    if (workspace / ".agent" / "manifest.json").is_file():
        try:
            audit = _run(
                runner,
                [str(python), "-m", "aef", "--workspace", str(workspace), "audit"],
                timeout=PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallRefused(
                "audit probe timed out",
                reason="audit_timeout",
                detail=str(exc),
            ) from exc
    return {
        "version_ok": version_ok,
        "version_output": (version.stdout or "").strip(),
        "reported_version": reported,
        "audit_ran": audit is not None,
        "audit_ok": None if audit is None else audit.returncode == 0,
    }


def current_interpreter() -> Path:
    return Path(sys.executable)
