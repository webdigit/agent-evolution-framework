"""Read-only runtime diagnosis. Never mutates .agent/ or an existing venv."""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any

from .runtime_discovery import (
    DECISION_BLOCKED,
    DECISION_INSTALL_REQUIRED,
    DECISION_OK,
    discover_runtime,
    find_local_wheels,
    host_architecture,
    host_platform,
    interpreter_label,
    workspace_contains,
)
from .upgrade_compat import installed_package_version

DOCTOR_RESULT_FIELDS = (
    "platform",
    "architecture",
    "interpreter",
    "discovery_method",
    "found_package_version",
    "expected_package_version",
    "workspace_compatible",
    "venv_status",
    "network_required",
    "local_artifact",
    "human_action_required",
    "install_command",
    "decision",
)


def _workspace_initialized(workspace: Path) -> bool | None:
    manifest = workspace / ".agent" / "manifest.json"
    if not manifest.exists():
        return False
    if not workspace_contains(workspace, manifest):
        return None
    return manifest.is_file()


def _hash_file(path: Path) -> str:
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


def classify_local_artifact(workspace: Path) -> tuple[str, Path | None]:
    wheels = find_local_wheels(workspace)
    if not wheels:
        return "absent", None
    wheel = wheels[0]
    expected = _expected_hash(wheel)
    if expected is None:
        return "available", wheel
    try:
        actual = _hash_file(wheel)
    except OSError:
        return "absent", None
    if actual == expected:
        return "available", wheel
    return "hash_mismatch", wheel


def _quote_command_part(part: str) -> str:
    if host_platform() == "windows":
        return '"' + part.replace('"', '\\"') + '"'
    return shlex.quote(part)


def proposed_install_command(*, wheel: Path | None, version: str, isolated_dir: str) -> str:
    python = "py -3.11" if host_platform() == "windows" else "python3"
    env = _quote_command_part(isolated_dir)
    interpreter = _quote_command_part(isolated_dir_python(isolated_dir))
    if wheel is not None:
        artifact = _quote_command_part(wheel.name)
        return f"{python} -m venv {env} && {interpreter} -m pip install --no-index {artifact}"
    pin = _quote_command_part(f"agent-evolution-framework=={version}")
    return f"{python} -m venv {env} && {interpreter} -m pip install {pin}"


def isolated_dir_python(isolated_dir: str) -> str:
    if host_platform() == "windows":
        return f"{isolated_dir}\\Scripts\\python.exe"
    return f"{isolated_dir}/bin/python"


def isolated_env_name(venv_status: str) -> str:
    if venv_status == "incompatible":
        return f".aef-venv-{host_platform()}"
    return ".aef-venv"


def diagnose_runtime(workspace: Path, **discovery_hooks: Any) -> dict[str, Any]:
    workspace = Path(workspace)
    discovered = discover_runtime(workspace, **discovery_hooks)
    if discovered["external_env"]:
        decision = DECISION_BLOCKED
    else:
        decision = discovered["decision"]
    artifact, wheel = classify_local_artifact(workspace)
    initialized = _workspace_initialized(workspace)
    version = discovered["expected_package_version"] or installed_package_version()
    env_name = isolated_env_name(discovered["venv_status"])
    network_required = artifact != "available"
    install_command = proposed_install_command(
        wheel=wheel if artifact == "available" else None,
        version=version,
        isolated_dir=env_name,
    )
    if decision == DECISION_OK:
        human_action = False
        network_required = False
        install_command = ""
    else:
        human_action = decision == DECISION_INSTALL_REQUIRED
    return {
        "platform": host_platform(),
        "architecture": host_architecture(),
        "interpreter": interpreter_label(),
        "discovery_method": discovered["discovery_method"],
        "found_package_version": discovered["found_package_version"],
        "expected_package_version": discovered["expected_package_version"],
        "workspace_compatible": initialized if initialized is not None else "unknown",
        "venv_status": discovered["venv_status"],
        "network_required": network_required,
        "local_artifact": artifact,
        "human_action_required": human_action,
        "install_command": install_command,
        "decision": decision,
    }
