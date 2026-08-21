"""Isolated AEF install after explicit --install consent. Never mutates .agent/state/."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .runtime_discovery import host_platform, inspect_venv_tree, parse_aef_version_output, workspace_contains
from .runtime_doctor import (
    candidate_isolated_env_paths,
    classify_local_artifact,
    dependency_wheels_present,
    diagnose_runtime,
    isolated_env_name,
    resolve_fresh_env_path,
    resolve_package_install_spec,
)

Runner = Callable[..., subprocess.CompletedProcess]

PROBE_TIMEOUT = 30
PIP_TIMEOUT = 300
VENV_TIMEOUT = 120
META_DETAIL_MAX = 2048


class InstallRefused(RuntimeError):
    """Install was not consented or cannot start safely."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        diagnosis: dict[str, Any] | None = None,
        detail: str | None = None,
        created: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.diagnosis = diagnosis
        self.detail = detail
        self.created = list(created or [])


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
    """Strip PIP_* and PYTHON* so host sitecustomize/path cannot hijack pip."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PIP_") and not key.startswith("PYTHON")
    }


def truncate_detail(text: str | None, limit: int = META_DETAIL_MAX) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit]


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


def _remove_env_tree(env_path: Path) -> bool:
    """Best-effort removal of a freshly created env. True if absent afterwards."""
    if not env_path.exists():
        return True
    try:
        shutil.rmtree(env_path)
    except OSError:
        return not env_path.exists()
    return not env_path.exists()


def _usable_isolated_python(env_path: Path, runner: Runner) -> Path | None:
    if inspect_venv_tree(env_path) != "compatible":
        return None
    python = venv_python(env_path)
    if not python.is_file():
        return None
    try:
        completed = _run(
            runner,
            [str(python), "-I", "-m", "aef", "--version"],
            timeout=PROBE_TIMEOUT,
            env=_pip_clean_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallRefused(
            "timed out probing existing isolated interpreter",
            reason="probe_timeout",
            detail=truncate_detail(str(exc)),
        ) from exc
    if completed.returncode == 0 and parse_aef_version_output(completed.stdout or ""):
        return python
    return None


def _candidate_env_paths(workspace: Path, venv_status: str) -> list[Path]:
    return candidate_isolated_env_paths(workspace, venv_status)


def _fresh_env_path(workspace: Path, venv_status: str) -> Path:
    """Choose a create target that does not reuse a pre-existing interpreter tree."""
    chosen = resolve_fresh_env_path(workspace, venv_status)
    if chosen is not None:
        return chosen
    raise InstallRefused(
        "refusing to reuse an existing environment without --reuse-env",
        reason="reuse_refused",
    )


def _create_venv(env_path: Path, runner: Runner) -> None:
    """Create venv via the same interpreter shown in the proposed command."""
    command = [sys.executable, "-m", "venv", str(env_path)]
    try:
        completed = _run(runner, command, timeout=VENV_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        _remove_env_tree(env_path)
        raise InstallRefused(
            "timed out creating isolated virtual environment",
            reason="venv_create_timeout",
            detail=truncate_detail(str(exc)),
        ) from exc
    except OSError as exc:
        _remove_env_tree(env_path)
        raise InstallRefused(
            "failed to create isolated virtual environment",
            reason="venv_create_failed",
            detail=truncate_detail(str(exc)),
        ) from exc
    if completed.returncode != 0:
        _remove_env_tree(env_path)
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit {completed.returncode}"
        raise InstallRefused(
            "failed to create isolated virtual environment",
            reason="venv_create_failed",
            detail=truncate_detail(detail),
        )


def install_isolated(
    workspace: Path,
    *,
    consented: bool,
    reuse_env: bool = False,
    runner: Runner | None = None,
    **discovery_hooks: Any,
) -> dict[str, Any]:
    """Create a new isolated env and install AEF. Never rewrite an existing venv."""
    if runner is None:
        runner = subprocess.run
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
    offline_ready = (
        artifact == "verified"
        and wheel is not None
        and dependency_wheels_present(wheel.parent)
    )
    if offline_ready:
        spec = resolve_package_install_spec(
            expected_package_version=diagnosis["expected_package_version"],
            artifact="verified",
            wheel=wheel,
        )
    else:
        spec = resolve_package_install_spec(
            expected_package_version=diagnosis["expected_package_version"],
            artifact="absent",
            wheel=None,
        )

    created_rel = env_path.name
    try:
        _create_venv(env_path, runner)
    except InstallRefused as exc:
        if exc.diagnosis is None:
            exc.diagnosis = diagnosis
        raise

    python = venv_python(env_path)
    command = [str(python), "-I", "-m", "pip", "install", *spec.pip_args]

    def _fail_after_create(reason: str, message: str, detail: str | None) -> None:
        cleaned = _remove_env_tree(env_path)
        created = [] if cleaned else [created_rel]
        raise InstallRefused(
            message,
            reason=reason,
            diagnosis=diagnosis,
            detail=truncate_detail(detail),
            created=created,
        )

    try:
        completed = _run(
            runner,
            command,
            timeout=PIP_TIMEOUT,
            env=_pip_clean_env(),
        )
    except subprocess.TimeoutExpired as exc:
        _fail_after_create("pip_timeout", "pip install timed out", str(exc))
        raise  # pragma: no cover
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit {completed.returncode}"
        _fail_after_create("pip_failed", "pip install failed", detail)
    return {
        "changed": True,
        "env_path": str(env_path),
        "python": str(python),
        "diagnosis": diagnosis,
        "created_env": True,
        "install_pin": spec.pin_version,
        "install_mode": spec.mode,
    }


def cleanup_failed_install(env_path: Path) -> bool:
    """Remove an env left after verify failure. True if absent afterwards."""
    return _remove_env_tree(env_path)


def verify_installed(
    python: Path,
    workspace: Path,
    *,
    expected_version: str | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    if runner is None:
        runner = subprocess.run
    clean = _pip_clean_env()
    try:
        version = _run(
            runner,
            [str(python), "-I", "-m", "aef", "--version"],
            timeout=PROBE_TIMEOUT,
            env=clean,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallRefused(
            "version probe timed out",
            reason="verify_timeout",
            detail=truncate_detail(str(exc)),
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
                [str(python), "-I", "-m", "aef", "--workspace", str(workspace), "audit"],
                timeout=PROBE_TIMEOUT,
                env=clean,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallRefused(
                "audit probe timed out",
                reason="audit_timeout",
                detail=truncate_detail(str(exc)),
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
