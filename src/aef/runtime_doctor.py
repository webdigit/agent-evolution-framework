"""Read-only runtime diagnosis. Never mutates .agent/ or an existing venv."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_discovery import (
    DECISION_BLOCKED,
    DECISION_INSTALL_REQUIRED,
    DECISION_OK,
    discover_runtime,
    host_architecture,
    host_platform,
    interpreter_label,
    select_local_wheel,
    workspace_contains,
)
from .upgrade_compat import installed_package_version

PYPI_INDEX_URL = "https://pypi.org/simple"
JSONSCHEMA_WHEEL_PREFIX = "jsonschema-"

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
    "blocked_cause",
    "blocked_path",
)


@dataclass(frozen=True)
class PackageInstallSpec:
    """Single source of truth for proposed and executed package install."""

    mode: str  # "wheel" | "pypi"
    pin_version: str
    wheel: Path | None
    find_links: Path | None
    pip_args: tuple[str, ...]


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


def dependency_wheels_present(directory: Path) -> bool:
    """Offline install needs at least jsonschema among dependency wheels."""
    if not directory.is_dir():
        return False
    for item in directory.iterdir():
        if item.is_file() and item.name.startswith(JSONSCHEMA_WHEEL_PREFIX) and item.suffix == ".whl":
            return True
    return False


def classify_local_artifact(
    workspace: Path,
    *,
    expected_version: str | None = None,
) -> tuple[str, Path | None, list[Path]]:
    """Return (classification, wheel, candidates).

    Classifications: absent | verified | available_unverified | hash_mismatch | ambiguous
    """
    selection = select_local_wheel(workspace, expected_version=expected_version)
    if selection["status"] == "ambiguous":
        return "ambiguous", None, list(selection["candidates"])
    if selection["status"] != "selected" or selection["wheel"] is None:
        return "absent", None, list(selection["candidates"])
    wheel = selection["wheel"]
    expected = _expected_hash(wheel)
    if expected is None:
        return "available_unverified", wheel, [wheel]
    try:
        actual = _hash_file(wheel)
    except OSError:
        return "absent", None, [wheel]
    if actual == expected:
        return "verified", wheel, [wheel]
    return "hash_mismatch", wheel, [wheel]


def resolve_package_install_spec(
    *,
    expected_package_version: str | None,
    artifact: str,
    wheel: Path | None,
) -> PackageInstallSpec:
    """Compute the one install specification shared by propose and execute."""
    pin = expected_package_version or installed_package_version()
    if artifact in {"verified", "available_unverified"} and wheel is not None:
        find_links = wheel.parent
        pip_args = (
            "--isolated",
            "--no-cache-dir",
            "--no-index",
            "--find-links",
            str(find_links),
            str(wheel),
        )
        return PackageInstallSpec(
            mode="wheel",
            pin_version=pin,
            wheel=wheel,
            find_links=find_links,
            pip_args=pip_args,
        )
    pip_args = (
        "--isolated",
        "--no-cache-dir",
        "--index-url",
        PYPI_INDEX_URL,
        f"agent-evolution-framework=={pin}",
    )
    return PackageInstallSpec(
        mode="pypi",
        pin_version=pin,
        wheel=None,
        find_links=None,
        pip_args=pip_args,
    )


def _quote_command_part(part: str) -> str:
    if host_platform() == "windows":
        return '"' + part.replace('"', '\\"') + '"'
    return shlex.quote(part)


def proposed_install_command_from_spec(spec: PackageInstallSpec, isolated_dir: str) -> str:
    python = "py -3.11" if host_platform() == "windows" else "python3"
    env = _quote_command_part(isolated_dir)
    interpreter = _quote_command_part(isolated_dir_python(isolated_dir))
    if spec.mode == "wheel" and spec.wheel is not None:
        find_links = _quote_command_part(str(spec.find_links))
        artifact = _quote_command_part(spec.wheel.name)
        return (
            f"{python} -m venv {env} && {interpreter} -m pip install "
            f"--isolated --no-cache-dir --no-index --find-links {find_links} {artifact}"
        )
    pin = _quote_command_part(f"agent-evolution-framework=={spec.pin_version}")
    index = _quote_command_part(PYPI_INDEX_URL)
    return (
        f"{python} -m venv {env} && {interpreter} -m pip install "
        f"--isolated --no-cache-dir --index-url {index} {pin}"
    )


def proposed_install_command(*, wheel: Path | None, version: str, isolated_dir: str) -> str:
    """Back-compat wrapper; prefer resolve_package_install_spec + from_spec."""
    artifact = "verified" if wheel is not None else "absent"
    spec = resolve_package_install_spec(
        expected_package_version=version,
        artifact=artifact,
        wheel=wheel,
    )
    return proposed_install_command_from_spec(spec, isolated_dir)


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
    decision = discovered["decision"]
    if discovered.get("external_env") and decision != DECISION_BLOCKED:
        decision = DECISION_BLOCKED
    expected = discovered["expected_package_version"]
    artifact, wheel, candidates = classify_local_artifact(
        workspace, expected_version=expected,
    )
    blocked_cause = discovered.get("blocked_cause")
    if artifact == "ambiguous" and decision != DECISION_BLOCKED:
        decision = DECISION_BLOCKED
        blocked_cause = "ambiguous_local_wheels"
    initialized = _workspace_initialized(workspace)
    env_name = isolated_env_name(discovered["venv_status"])
    usable_wheel = artifact in {"verified", "available_unverified"}
    offline_ready = (
        artifact == "verified"
        and wheel is not None
        and dependency_wheels_present(wheel.parent)
    )
    if decision == DECISION_OK:
        human_action = False
        network_required = False
        install_command = ""
        spec = None
    elif decision == DECISION_BLOCKED:
        human_action = False
        network_required = not offline_ready
        install_command = ""
        spec = None
    else:
        human_action = decision == DECISION_INSTALL_REQUIRED
        spec = resolve_package_install_spec(
            expected_package_version=expected,
            artifact=artifact if usable_wheel else "absent",
            wheel=wheel if usable_wheel else None,
        )
        network_required = not offline_ready
        install_command = proposed_install_command_from_spec(spec, env_name)
    result = {
        "platform": host_platform(),
        "architecture": host_architecture(),
        "interpreter": interpreter_label(),
        "discovery_method": discovered["discovery_method"],
        "found_package_version": discovered["found_package_version"],
        "expected_package_version": expected,
        "workspace_compatible": initialized if initialized is not None else "unknown",
        "venv_status": discovered["venv_status"],
        "network_required": network_required,
        "local_artifact": artifact,
        "human_action_required": human_action,
        "install_command": install_command,
        "decision": decision,
        "blocked_cause": blocked_cause,
        "blocked_path": (
            discovered.get("blocked_path")
            if blocked_cause == "invalid_expected_package_version"
            else (
                ",".join(path.name for path in candidates)
                if blocked_cause == "ambiguous_local_wheels"
                else None
            )
        ),
    }
    return result
