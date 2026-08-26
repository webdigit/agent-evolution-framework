"""Read-only runtime diagnosis. Never mutates .agent/ or an existing venv."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_confined_io import (
    dependency_wheel_is_usable,
    proposed_install_path_is_safe,
    read_bytes_confined,
    read_text_confined,
    workspace_contains,
)
from .runtime_discovery import (
    DECISION_BLOCKED,
    DECISION_OK,
    discover_runtime,
    found_package_version,
    host_architecture,
    host_platform,
    interpreter_label,
    select_local_wheel,
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
    "running_module_version",
    "declared_version_source",
    "declared_env_root",
    "declared_env_mismatch",
    "workspace_compatible",
    "venv_status",
    "network_required",
    "local_artifact",
    "install_command",
    "decision",
    "blocked_cause",
    "blocked_path",
    "observations",
    "offline_basis",
)

# Marker version for the managed consumer card at docs/runtime.md.
# Independent of GUIDANCE_VERSION on catalog doors.
RUNTIME_CARD_VERSION = "1.0.0"


@dataclass(frozen=True)
class PackageInstallSpec:
    """Single source of truth for proposed package install commands."""

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


def _hash_file(path: Path, workspace: Path) -> str | None:
    payload = read_bytes_confined(workspace, path, site="local_wheel.sha256")
    if payload is None:
        return None
    return hashlib.sha256(payload).hexdigest()


def _read_checksum_sidecar(path: Path, workspace: Path) -> str | None:
    return read_text_confined(workspace, path, site="checksum.sidecar")


def _expected_hash(wheel: Path, workspace: Path) -> str | None:
    """Return a digest from a sidecar supplied alongside the wheel.

  This attests internal consistency of a co-located pair only — not an
  independent trust anchor.
    """
    sibling = Path(str(wheel) + ".sha256")
    sums = wheel.parent / "SHA256SUMS.txt"
    for candidate in (sibling, sums):
        if not candidate.is_file():
            continue
        text = _read_checksum_sidecar(candidate, workspace)
        if text is None:
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].endswith(wheel.name):
                return parts[0].lower()
            if len(parts) == 1 and candidate == sibling:
                return parts[0].lower()
    return None


def dependency_wheels_present(directory: Path, workspace: Path) -> bool:
    """Offline install needs a confined jsonschema-*.whl filename (archive not opened)."""
    if not directory.is_dir():
        return False
    for item in sorted(directory.iterdir(), key=lambda path: path.name):
        if not item.is_file() or item.suffix != ".whl":
            continue
        if not item.name.startswith(JSONSCHEMA_WHEEL_PREFIX):
            continue
        if dependency_wheel_is_usable(workspace, item):
            return True
    return False


def classify_local_artifact(
    workspace: Path,
    *,
    expected_version: str | None = None,
) -> tuple[str, Path | None, list[Path]]:
    """Return (classification, wheel, candidates).

    Classifications: absent | checksum_matched | available_unverified |
    hash_mismatch | ambiguous
    """
    selection = select_local_wheel(workspace, expected_version=expected_version)
    if selection["status"] == "ambiguous":
        return "ambiguous", None, list(selection["candidates"])
    if selection["status"] != "selected" or selection["wheel"] is None:
        return "absent", None, list(selection["candidates"])
    wheel = selection["wheel"]
    expected = _expected_hash(wheel, workspace)
    if expected is None:
        return "available_unverified", wheel, [wheel]
    actual = _hash_file(wheel, workspace)
    if actual is None:
        return "available_unverified", wheel, [wheel]
    if actual == expected:
        return "checksum_matched", wheel, [wheel]
    return "hash_mismatch", wheel, [wheel]


def resolve_package_install_spec(
    *,
    expected_package_version: str | None,
    artifact: str,
    wheel: Path | None,
) -> PackageInstallSpec:
    """Compute the install specification for a human-copyable proposal.

    Offline ``--no-index`` is only proposed for ``checksum_matched`` wheels.
    ``available_unverified`` falls through to the PyPI pin.
    """
    pin = expected_package_version or installed_package_version()
    if artifact == "checksum_matched" and wheel is not None:
        abs_wheel = wheel.resolve()
        find_links = abs_wheel.parent
        pip_args = (
            "--isolated",
            "--no-cache-dir",
            "--no-index",
            "--find-links",
            str(find_links),
            str(abs_wheel),
        )
        return PackageInstallSpec(
            mode="wheel",
            pin_version=pin,
            wheel=abs_wheel,
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
    """Quote for a human-copyable shell/cmd line."""
    if host_platform() == "windows":
        return '"' + part.replace('"', '""') + '"'
    return shlex.quote(part)


def _path_for_command(path: Path | str, workspace: Path | None) -> str:
    """Return a workspace-relative posix path, never a home-bearing absolute."""
    candidate = Path(path)
    if workspace is None:
        return candidate.as_posix() if not candidate.is_absolute() else candidate.name
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"install-command path {candidate} is outside the workspace"
        ) from exc


def proposed_install_command_from_spec(
    spec: PackageInstallSpec,
    isolated_dir: str,
    *,
    python_executable: str | None = None,
    workspace: Path | None = None,
) -> str:
    python = _quote_command_part(python_executable or "python")
    env = _quote_command_part(isolated_dir)
    interpreter = _quote_command_part(isolated_dir_python(isolated_dir))
    if spec.mode == "wheel" and spec.wheel is not None:
        find_links = _quote_command_part(
            _path_for_command(spec.find_links, workspace) if spec.find_links else "."
        )
        artifact = _quote_command_part(_path_for_command(spec.wheel, workspace))
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


def isolated_dir_python(isolated_dir: str) -> str:
    if host_platform() == "windows":
        return f"{isolated_dir}\\Scripts\\python.exe"
    return f"{isolated_dir}/bin/python"


def isolated_env_name(venv_status: str) -> str:
    if venv_status == "incompatible":
        return f".aef-venv-{host_platform()}"
    return ".aef-venv"


def candidate_isolated_env_paths(workspace: Path, venv_status: str) -> list[Path]:
    """Ordered install targets: primary then platform-suffixed name."""
    primary = workspace / isolated_env_name(venv_status)
    paths = [primary]
    platform_named = workspace / isolated_env_name("incompatible")
    if platform_named != primary:
        paths.append(platform_named)
    return paths


def resolve_proposed_env_path(
    workspace: Path,
    venv_status: str,
) -> tuple[Path | None, str | None]:
    """Return a safe install target inside the workspace, or a blocked cause."""
    candidates = list(candidate_isolated_env_paths(workspace, venv_status))
    suffix = host_platform()
    for index in range(2, 100):
        candidates.append(workspace / f".aef-venv-{suffix}-{index}")
    candidates.append(workspace / f".aef-venv-{suffix}-new")
    for candidate in candidates:
        if proposed_install_path_is_safe(workspace, candidate):
            return candidate, None
    return None, "unsafe_install_target"


def diagnose_runtime(workspace: Path, **discovery_hooks: Any) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    discovered = discover_runtime(workspace, **discovery_hooks)
    decision = discovered["decision"]
    if discovered.get("external_env") and decision != DECISION_BLOCKED:
        decision = DECISION_BLOCKED
    expected = discovered["expected_package_version"]
    artifact, wheel, candidates = classify_local_artifact(
        workspace, expected_version=expected,
    )
    blocked_cause = discovered.get("blocked_cause")
    observations: list[str] = []
    running_module_version = found_package_version(imported=True)
    declared_source = discovered.get("declared_version_source")
    declared_mismatch = discovered.get("declared_env_mismatch")
    declared_env_root = discovered.get("declared_env_root")
    declared_source_rel = None
    if isinstance(declared_source, Path):
        try:
            declared_source_rel = declared_source.relative_to(workspace).as_posix()
        except ValueError:
            declared_source_rel = declared_source.as_posix()
    if discovered["discovery_method"] == "declared_env" and declared_source_rel:
        observations.append(f"declared_env_version_source:{declared_source_rel}")
        if running_module_version:
            observations.append(f"running_module_version:{running_module_version}")
        observations.append("declared_env_tree_read:unverified")
    if declared_mismatch:
        observations.append(
            "declared_env_mismatch:"
            f"{declared_mismatch['path']}={declared_mismatch['version']}",
        )
    if artifact == "ambiguous":
        if decision == DECISION_OK:
            observations.append("ambiguous_local_wheels")
        elif decision != DECISION_BLOCKED:
            decision = DECISION_BLOCKED
            blocked_cause = "ambiguous_local_wheels"
    initialized = _workspace_initialized(workspace)
    env_path, install_issue = resolve_proposed_env_path(workspace, discovered["venv_status"])
    offline_ready = (
        artifact == "checksum_matched"
        and wheel is not None
        and dependency_wheels_present(wheel.parent, workspace)
    )
    offline_basis = "self_attested_checksum" if offline_ready else None
    if decision == DECISION_OK:
        network_required = False
        install_command = ""
    elif decision == DECISION_BLOCKED:
        network_required = False
        install_command = ""
    else:
        if env_path is None:
            decision = DECISION_BLOCKED
            blocked_cause = install_issue or "unsafe_install_target"
            network_required = False
            install_command = ""
        elif offline_ready:
            spec = resolve_package_install_spec(
                expected_package_version=expected,
                artifact="checksum_matched",
                wheel=wheel,
            )
            network_required = False
            install_command = proposed_install_command_from_spec(
                spec,
                _path_for_command(env_path, workspace),
                workspace=workspace,
            )
        else:
            spec = resolve_package_install_spec(
                expected_package_version=expected,
                artifact="absent",
                wheel=None,
            )
            network_required = True
            install_command = proposed_install_command_from_spec(
                spec,
                _path_for_command(env_path, workspace),
                workspace=workspace,
            )
    result = {
        "platform": host_platform(),
        "architecture": host_architecture(),
        "interpreter": interpreter_label(),
        "discovery_method": discovered["discovery_method"],
        "found_package_version": discovered["found_package_version"],
        "expected_package_version": expected,
        "running_module_version": running_module_version,
        "declared_version_source": declared_source_rel,
        "declared_env_root": declared_env_root,
        "declared_env_mismatch": declared_mismatch,
        "workspace_compatible": initialized,
        "venv_status": discovered["venv_status"],
        "network_required": network_required,
        "local_artifact": artifact,
        "install_command": install_command,
        "decision": decision,
        "blocked_cause": blocked_cause,
        "blocked_path": (
            discovered.get("blocked_path")
            if blocked_cause in {
                "invalid_expected_package_version",
                "external_env",
                "unsafe_install_target",
            }
            else (
                ",".join(path.name for path in candidates)
                if blocked_cause == "ambiguous_local_wheels"
                else None
            )
        ),
        "observations": observations,
        "offline_basis": offline_basis,
    }
    assert set(result) == set(DOCTOR_RESULT_FIELDS)
    return result


def _format_runtime_card_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        if not value:
            return "—"
        return ", ".join(f"{key}={value[key]}" for key in sorted(value))
    if isinstance(value, (list, tuple)):
        if not value:
            return "—"
        return ", ".join(str(item) for item in value)
    text = str(value)
    return text if text else "—"


def render_runtime_card(doctor_result: dict[str, Any]) -> str:
    """Pure deterministic markdown body for the managed runtime card.

    Same doctor result → same text. No filesystem I/O, no timestamps, no
    host values outside the doctor result. A written card that diverges from
    a fresh render is **périmé** (stale), never catalog-tampered.
    """
    required = set(DOCTOR_RESULT_FIELDS)
    missing = required - set(doctor_result)
    if missing:
        raise ValueError(f"doctor_result missing fields: {sorted(missing)}")
    extra = set(doctor_result) - required
    if extra:
        raise ValueError(f"doctor_result unexpected fields: {sorted(extra)}")

    decision = doctor_result["decision"]
    install_command = doctor_result.get("install_command") or ""
    if decision == DECISION_OK:
        action_lines = [
            "No install action is required (`decision: OK`).",
        ]
    elif install_command:
        action_lines = [
            "Proposed install command for this host (review before running):",
            "",
            "```console",
            install_command,
            "```",
        ]
    else:
        cause = doctor_result.get("blocked_cause") or "unknown"
        action_lines = [
            f"Diagnosis is blocked (`blocked_cause: {cause}`). "
            "No install command is proposed.",
        ]

    lines = [
        "# Runtime environment map",
        "",
        "Snapshot of what `aef doctor` reports for this workspace host.",
        "It is not a live view. Regenerate with `aef integrate runtime`.",
        "When the file no longer matches the host, it is **périmé** (stale) —",
        "regenerate; do not treat divergence as catalog tampering.",
        "",
        "Trust : tree read only (pip install not verified)",
        "",
        "## Environment",
        "",
    ]
    for field in DOCTOR_RESULT_FIELDS:
        lines.append(f"- `{field}`: {_format_runtime_card_value(doctor_result[field])}")
    lines.extend(["", "## Action", ""])
    lines.extend(action_lines)
    lines.extend(
        [
            "",
            "## Limits of this attestation",
            "",
            "- Tree read of declared env paths is not a verified install check.",
            "- `pip install` success is never attested here.",
            "- Paths are workspace-relative when possible; this card must not",
            "  embed personal home directories as authoritative identity.",
            "",
            "## Refresh",
            "",
            "```console",
            "aef integrate runtime",
            "aef doctor",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def wrap_runtime_segment(card_body: str) -> bytes:
    """Wrap a rendered card body in RUNTIME managed markers (UTF-8 bytes)."""
    body = card_body if card_body.endswith("\n") else f"{card_body}\n"
    return (
        f'<!-- AEF:RUNTIME:BEGIN version="{RUNTIME_CARD_VERSION}" -->\n'
        f"{body}"
        f"<!-- AEF:RUNTIME:END -->\n"
    ).encode("utf-8")
