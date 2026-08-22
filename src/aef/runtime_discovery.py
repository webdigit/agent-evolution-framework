"""Pure runtime discovery. Never spawn a binary from a foreign venv."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any, Callable

from ._version import __version__
from .filesystem import is_link_or_reparse_point
from .runtime_confined_io import (
    confined_file_size,
    read_text_confined,
    workspace_contains,
)
from .strict_json import DuplicateJSONKeyError, reject_duplicate_keys

DECISION_OK = "OK"
DECISION_INSTALL_REQUIRED = "INSTALL_REQUIRED"
DECISION_BLOCKED = "BLOCKED"
INSTALL_REQUIRED_EXIT = 8

RUNTIME_REQUIREMENTS_PATH = ".agent/runtime-requirements.json"
CANDIDATE_VENV_NAMES = (".aef-venv", ".venv", "venv")
WHEEL_NAME_PREFIX = "agent_evolution_framework-"
VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.M)
SHELL_UNSAFE_VERSION = re.compile(r'[&|;<>`$"\'\\\s\n\r]')
PEP440_VERSION = re.compile(
    r"^(?:\d+!)?"
    r"(?:[0-9]+(?:\.[0-9A-Za-z]+)*|[0-9]*\.[0-9]+(?:\.[0-9A-Za-z]+)*)"
    r"(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)\d*)?"
    r"(?:\.post[0-9]+)?"
    r"(?:\.dev[0-9]+)?"
    r"(?:\+[a-zA-Z0-9.]+)?$"
)
WINDOWS_HOME = re.compile(r"^[A-Za-z]:[\\/]")


def host_platform() -> str:
    if os.name == "nt":
        return "windows"
    if platform.system().lower() == "darwin":
        return "macos"
    return "linux"


def host_architecture() -> str:
    return (platform.machine() or "unknown").lower() or "unknown"


def interpreter_label() -> str:
    return f"{platform.python_implementation()}-{platform.python_version()}"


def is_pep440_version_token(value: str) -> bool:
    """Accept PEP 440 public versions; reject shell metacharacters."""
    if not value or SHELL_UNSAFE_VERSION.search(value):
        return False
    if not value.isascii():
        return False
    if not PEP440_VERSION.fullmatch(value):
        return False
    core = value.split("+", 1)[0].split("!", 1)[-1]
    for marker in ("alpha", "beta", "preview", "post", "dev", "rc"):
        idx = core.lower().find(marker)
        if idx > 0:
            core = core[:idx]
    for marker in ("a", "b", "c"):
        if marker in core.lower() and not core.lower().startswith(marker):
            idx = core.lower().rfind(marker)
            if idx > 0 and core[idx - 1].isdigit():
                core = core[:idx]
    for part in core.split("."):
        if part and not any(ch.isdigit() for ch in part):
            return False
    return True


def parse_pyvenv_cfg(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


def inspect_venv_tree(root: Path, workspace: Path) -> str:
    """Classify a directory tree without executing anything inside it."""
    if not root.exists() or not root.is_dir():
        return "absent"
    cfg = root / "pyvenv.cfg"
    has_win_python = (root / "Scripts" / "python.exe").is_file()
    has_posix_python = (root / "bin" / "python").is_file() or (
        root / "bin" / "python3"
    ).is_file()
    home = ""
    if cfg.is_file():
        text = read_text_confined(workspace, cfg, site="declared_env.pyvenv_cfg")
        if text is None:
            return "unknown"
        try:
            home = parse_pyvenv_cfg(text).get("home", "")
        except (ValueError, TypeError):
            return "unknown"
    windows_home = bool(WINDOWS_HOME.match(home)) or home.startswith("\\\\")
    posix_home = home.startswith("/")
    host = host_platform()
    if host == "windows":
        if has_posix_python and not has_win_python:
            return "incompatible"
        if posix_home and not windows_home:
            return "incompatible"
        if has_win_python:
            return "compatible"
    else:
        if has_win_python and not has_posix_python:
            return "incompatible"
        if windows_home:
            return "incompatible"
        if has_posix_python:
            return "compatible"
    if cfg.is_file() or (root / "Scripts").is_dir() or (root / "bin").is_dir():
        return "unknown"
    return "absent"


def module_importable() -> bool:
    try:
        import aef  # noqa: F401
    except ImportError:
        return False
    return True


def found_package_version(*, imported: bool | None = None) -> str | None:
    if imported is False:
        return None
    if imported is None and not module_importable():
        return None
    return __version__


def _read_bounded_utf8(path: Path, workspace: Path, *, site: str) -> str | None:
    return read_text_confined(workspace, path, site=site)


def read_expected_package_version(workspace: Path) -> dict[str, Any]:
    """Distinguish absent / valid / invalid expected package version."""
    rel = RUNTIME_REQUIREMENTS_PATH
    path = workspace / rel
    if not path.is_file():
        return {"status": "absent", "value": None, "path": rel}
    text = _read_bounded_utf8(path, workspace, site="agent.runtime_requirements")
    if text is None:
        return {"status": "invalid", "value": None, "path": rel}
    try:
        payload = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except (json.JSONDecodeError, UnicodeError, DuplicateJSONKeyError):
        return {"status": "invalid", "value": None, "path": rel}
    if not isinstance(payload, dict):
        return {"status": "invalid", "value": None, "path": rel}
    if "expected_package_version" not in payload:
        return {"status": "invalid", "value": None, "path": rel}
    value = payload["expected_package_version"]
    if isinstance(value, str) and value.strip():
        stripped = value.strip()
        if is_pep440_version_token(stripped):
            return {"status": "valid", "value": stripped, "path": rel}
        return {"status": "invalid", "value": None, "path": rel}
    return {"status": "invalid", "value": None, "path": rel}


def _aef_package_roots(venv: Path) -> list[Path]:
    roots = [venv / "Lib" / "site-packages" / "aef"]
    lib = venv / "lib"
    if lib.is_dir():
        for child in sorted(lib.iterdir(), key=lambda item: item.name):
            if child.name.startswith("python"):
                roots.append(child / "site-packages" / "aef")
    return roots


def venv_has_aef_package(venv: Path) -> bool:
    """Detect an installed AEF tree, including namespace packages without __init__.py."""
    for root in _aef_package_roots(venv):
        if (root / "_version.py").is_file():
            return True
    return False


def read_aef_version_from_tree(venv: Path, workspace: Path) -> tuple[str, Path] | None:
    for root in _aef_package_roots(venv):
        version_file = root / "_version.py"
        if not version_file.is_file():
            continue
        text = _read_bounded_utf8(version_file, workspace, site="declared_env.version_file")
        if text is None:
            continue
        match = VERSION_PATTERN.search(text)
        if not match:
            continue
        version = match.group(1)
        if not is_pep440_version_token(version):
            continue
        return version, version_file
    return None


def declared_env_install_evidence(
    venv: Path,
    pkg_root: Path,
    workspace: Path,
) -> list[str]:
    """Observable, content-checked hints — names alone are not enough."""
    evidence: list[str] = []
    site_packages = pkg_root.parent
    if site_packages.is_dir():
        for item in sorted(site_packages.iterdir(), key=lambda path: path.name):
            if not item.is_dir() or not item.name.endswith(".dist-info"):
                continue
            base = item.name[: -len(".dist-info")]
            if base not in {"aef",} and not base.startswith("agent_evolution_framework"):
                continue
            metadata = item / "METADATA"
            wheel_file = item / "WHEEL"
            if not metadata.is_file() or not wheel_file.is_file():
                continue
            metadata_text = read_text_confined(
                workspace, metadata, site="declared_env.version_file",
            )
            wheel_text = read_text_confined(
                workspace, wheel_file, site="declared_env.version_file",
            )
            if metadata_text is None or wheel_text is None:
                continue
            if not metadata_text.strip() or not wheel_text.strip():
                continue
            if "Name:" not in metadata_text or "Wheel-Version:" not in wheel_text:
                continue
            evidence.append("dist-info-metadata-wheel")
            record = item / "RECORD"
            if record.is_file():
                record_text = read_text_confined(
                    workspace, record, site="declared_env.version_file",
                )
                if record_text and record_text.strip():
                    evidence.append("record-nonempty")
            break
    if host_platform() == "windows":
        console = venv / "Scripts" / "aef.exe"
    else:
        console = venv / "bin" / "aef"
    size = confined_file_size(workspace, console, site="declared_env.console_script")
    if size is not None and size > 0:
        evidence.append("console_script-nonempty")
    return evidence


def external_declared_env_path(workspace: Path) -> str | None:
    for candidate in find_declared_envs(workspace):
        if not workspace_contains(workspace, candidate):
            try:
                rel = candidate.relative_to(workspace.resolve()).as_posix()
            except ValueError:
                rel = candidate.name
            try:
                target = candidate.resolve().as_posix()
            except OSError:
                target = "unresolved"
            return f"{rel} -> {target}"
    return None


def find_declared_envs(workspace: Path) -> list[Path]:
    found: list[Path] = []
    for name in CANDIDATE_VENV_NAMES:
        candidate = workspace / name
        if candidate.exists():
            found.append(candidate)
    suffix = f".aef-venv-{host_platform()}"
    extra = workspace / suffix
    if extra.exists():
        found.append(extra)
    return found


def summarize_venv_status(workspace: Path) -> str:
    statuses = []
    for candidate in find_declared_envs(workspace):
        if not workspace_contains(workspace, candidate):
            return "blocked"
        statuses.append(inspect_venv_tree(candidate, workspace))
    if not statuses:
        return "absent"
    if "incompatible" in statuses and "compatible" not in statuses:
        return "incompatible"
    if "compatible" in statuses:
        return "compatible"
    if all(item == "absent" for item in statuses):
        return "absent"
    return "unknown"


def wheel_version_from_filename(name: str) -> str | None:
    if not name.startswith(WHEEL_NAME_PREFIX) or not name.endswith(".whl"):
        return None
    body = name[len(WHEEL_NAME_PREFIX) : -4]
    parts = body.split("-")
    if len(parts) < 4:
        return None
    version = "-".join(parts[:-3])
    if is_pep440_version_token(version):
        return version
    return None


def find_local_wheels(workspace: Path) -> list[Path]:
    wheels: list[Path] = []
    for folder in (workspace, workspace / "dist"):
        if not folder.is_dir():
            continue
        if folder != workspace and not workspace_contains(workspace, folder):
            continue
        for item in folder.iterdir():
            if item.is_file() and item.name.startswith(WHEEL_NAME_PREFIX) and item.suffix == ".whl":
                if workspace_contains(workspace, item):
                    wheels.append(item)
    return sorted(wheels)


def select_local_wheel(
    workspace: Path,
    *,
    expected_version: str | None,
) -> dict[str, Any]:
    """Pick a single wheel or report ambiguity / absence."""
    wheels = find_local_wheels(workspace)
    if not wheels:
        return {"status": "absent", "wheel": None, "candidates": []}
    if expected_version is not None:
        matched = [
            wheel
            for wheel in wheels
            if wheel_version_from_filename(wheel.name) == expected_version
        ]
        if len(matched) == 1:
            return {"status": "selected", "wheel": matched[0], "candidates": matched}
        if len(matched) > 1:
            return {
                "status": "ambiguous",
                "wheel": None,
                "candidates": matched,
            }
        if len(wheels) > 1:
            return {
                "status": "ambiguous",
                "wheel": None,
                "candidates": wheels,
            }
        return {"status": "absent", "wheel": None, "candidates": wheels}
    if len(wheels) == 1:
        return {"status": "selected", "wheel": wheels[0], "candidates": wheels}
    return {
        "status": "ambiguous",
        "wheel": None,
        "candidates": wheels,
    }


def discover_runtime(
    workspace: Path,
    *,
    can_import: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Deterministic discovery. Never executes a PATH or foreign-venv binary.

    When a declared project environment carries a readable AEF version that
    satisfies ``expected_package_version``, it wins over the running
    interpreter (``python_module``). Otherwise discovery falls back to the
    current module rather than reporting a stale declared env.
    """
    imported = (can_import or module_importable)()
    venv_status = summarize_venv_status(workspace)
    expected_info = read_expected_package_version(workspace)
    expected = expected_info["value"] if expected_info["status"] == "valid" else None
    declared_version = None
    declared_version_source: Path | None = None
    declared_env_root: Path | None = None
    declared_env_mismatch: dict[str, str] | None = None
    if venv_status == "compatible":
        for candidate in find_declared_envs(workspace):
            if inspect_venv_tree(candidate, workspace) != "compatible":
                continue
            if not venv_has_aef_package(candidate):
                continue
            tree_read = read_aef_version_from_tree(candidate, workspace)
            if tree_read is None:
                continue
            version, version_source = tree_read
            if expected is not None and version != expected:
                if declared_env_mismatch is None:
                    try:
                        rel = candidate.relative_to(workspace.resolve()).as_posix()
                    except ValueError:
                        rel = candidate.name
                    declared_env_mismatch = {"path": rel, "version": version}
                continue
            declared_version = version
            declared_version_source = version_source
            declared_env_root = candidate
            break
    if declared_version is not None:
        method = "declared_env"
        version = declared_version
    elif imported:
        method = "python_module"
        version = found_package_version(imported=True)
    else:
        method = "none"
        version = None
    if expected_info["status"] == "invalid":
        install_evidence = None
        if declared_env_root is not None and declared_version_source is not None:
            pkg_root = declared_version_source.parent
            install_evidence = declared_env_install_evidence(
                declared_env_root, pkg_root, workspace,
            )
        return {
            "discovery_method": method,
            "found_package_version": version,
            "expected_package_version": None,
            "expected_version_status": "invalid",
            "venv_status": venv_status if venv_status != "blocked" else "blocked",
            "external_env": venv_status == "blocked",
            "blocked_cause": "invalid_expected_package_version",
            "blocked_path": expected_info["path"],
            "declared_version_source": declared_version_source,
            "declared_env_root": declared_env_root,
            "declared_env_install_evidence": install_evidence,
            "declared_env_mismatch": declared_env_mismatch,
            "decision": DECISION_BLOCKED,
        }
    version_ok = version is not None and (expected is None or version == expected)
    usable = method != "none" and version_ok and venv_status != "blocked"
    if venv_status == "blocked":
        decision = DECISION_BLOCKED
        blocked_cause = "external_env"
        blocked_path = external_declared_env_path(workspace)
    elif usable:
        decision = DECISION_OK
        blocked_cause = None
        blocked_path = None
    else:
        decision = DECISION_INSTALL_REQUIRED
        blocked_cause = None
        blocked_path = None
    install_evidence = None
    if declared_env_root is not None and declared_version_source is not None:
        pkg_root = declared_version_source.parent
        install_evidence = declared_env_install_evidence(
            declared_env_root, pkg_root, workspace,
        )
    return {
        "discovery_method": method,
        "found_package_version": version,
        "expected_package_version": expected,
        "expected_version_status": expected_info["status"],
        "venv_status": venv_status,
        "external_env": venv_status == "blocked",
        "blocked_cause": blocked_cause,
        "blocked_path": (
            expected_info["path"]
            if blocked_cause == "invalid_expected_package_version"
            else blocked_path
        ),
        "declared_version_source": declared_version_source,
        "declared_env_root": declared_env_root,
        "declared_env_install_evidence": install_evidence,
        "declared_env_mismatch": declared_env_mismatch,
        "decision": decision,
    }
