"""Pure runtime discovery. Never spawn a binary from a foreign venv."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ._version import __version__

DECISION_OK = "OK"
DECISION_INSTALL_REQUIRED = "INSTALL_REQUIRED"
DECISION_BLOCKED = "BLOCKED"
DECISION_ERROR = "ERROR"
INSTALL_REQUIRED_EXIT = 8

RUNTIME_REQUIREMENTS_PATH = ".agent/runtime-requirements.json"
CANDIDATE_VENV_NAMES = (".aef-venv", ".venv", "venv")
WHEEL_NAME_PREFIX = "agent_evolution_framework-"
VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.M)
VERSION_TOKEN = re.compile(r"^\d+\.\d+\.\d+[0-9A-Za-z._+-]*$")
WINDOWS_HOME = re.compile(r"^[A-Za-z]:[\\/]")
PATH_VERSION_TIMEOUT = 10

Runner = Callable[..., subprocess.CompletedProcess]


def host_platform() -> str:
    if os.name == "nt":
        return "windows"
    if platform.system().lower() == "darwin":
        return "macos"
    return "linux"


def host_architecture() -> str:
    return (platform.machine() or "unknown").lower() or "unknown"


def interpreter_label(executable: str | None = None) -> str:
    _ = executable
    return f"{platform.python_implementation()}-{platform.python_version()}"


def parse_pyvenv_cfg(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


def inspect_venv_tree(root: Path) -> str:
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
        try:
            home = parse_pyvenv_cfg(cfg.read_text(encoding="utf-8")).get("home", "")
        except OSError:
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


def path_binary_compatible(path: Path) -> bool:
    name = path.name.lower()
    if host_platform() == "windows":
        return name in {"aef.exe", "aef.cmd", "aef.bat", "aef"}
    return name == "aef"


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


def parse_aef_version_output(text: str) -> str | None:
    """Strictly parse `aef --version` / package version output."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = lines[0]
    parts = line.split()
    if len(parts) == 2 and parts[0].lower() == "aef":
        candidate = parts[1]
    elif len(parts) == 1:
        candidate = parts[0]
    else:
        return None
    if VERSION_TOKEN.fullmatch(candidate):
        return candidate
    return None


def probe_path_package_version(
    binary: Path,
    *,
    runner: Runner = subprocess.run,
    timeout: float = PATH_VERSION_TIMEOUT,
) -> str | None:
    """Execute PATH binary --version. Never invent an unobserved version."""
    try:
        completed = runner(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return parse_aef_version_output(completed.stdout or "")


def read_expected_package_version(workspace: Path) -> dict[str, Any]:
    """Distinguish absent / valid / invalid expected package version."""
    rel = RUNTIME_REQUIREMENTS_PATH
    path = workspace / rel
    if not path.is_file():
        return {"status": "absent", "value": None, "path": rel}
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"status": "invalid", "value": None, "path": rel}
    if not isinstance(payload, dict):
        return {"status": "invalid", "value": None, "path": rel}
    if "expected_package_version" not in payload:
        return {"status": "invalid", "value": None, "path": rel}
    value = payload["expected_package_version"]
    if isinstance(value, str) and value.strip():
        return {"status": "valid", "value": value.strip(), "path": rel}
    return {"status": "invalid", "value": None, "path": rel}


def _aef_package_roots(venv: Path) -> list[Path]:
    roots = [venv / "Lib" / "site-packages" / "aef"]
    lib = venv / "lib"
    if lib.is_dir():
        for child in lib.iterdir():
            if child.name.startswith("python"):
                roots.append(child / "site-packages" / "aef")
    return roots


def venv_has_aef_package(venv: Path) -> bool:
    return any((root / "__init__.py").is_file() for root in _aef_package_roots(venv))


def read_aef_version_from_tree(venv: Path) -> str | None:
    for root in _aef_package_roots(venv):
        version_file = root / "_version.py"
        if not version_file.is_file():
            continue
        try:
            text = version_file.read_text(encoding="utf-8")
        except OSError:
            continue
        match = VERSION_PATTERN.search(text)
        if match:
            return match.group(1)
    return None


def workspace_contains(workspace: Path, target: Path) -> bool:
    try:
        root = workspace.resolve()
        resolved = target.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return True


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
        statuses.append(inspect_venv_tree(candidate))
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
    if VERSION_TOKEN.fullmatch(version):
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
    path_lookup=None,
    can_import=None,
    path_version_runner: Runner | None = None,
) -> dict[str, Any]:
    """Deterministic discovery. Never executes a foreign venv binary."""
    lookup = path_lookup or shutil.which
    imported = (can_import or module_importable)()
    path_hit = lookup("aef") or lookup("aef.exe")
    path_version = None
    path_ok = False
    if path_hit:
        binary = Path(path_hit)
        if path_binary_compatible(binary):
            path_version = probe_path_package_version(
                binary,
                runner=path_version_runner or subprocess.run,
            )
            path_ok = path_version is not None
    venv_status = summarize_venv_status(workspace)
    declared_ready = False
    declared_version = None
    if venv_status == "compatible":
        for candidate in find_declared_envs(workspace):
            if inspect_venv_tree(candidate) == "compatible" and venv_has_aef_package(candidate):
                declared_ready = True
                declared_version = read_aef_version_from_tree(candidate)
                break
    if path_ok:
        method = "path"
        version = path_version
    elif imported:
        method = "python_module"
        version = found_package_version(imported=True)
    elif declared_ready:
        method = "declared_env"
        version = declared_version
    else:
        method = "none"
        version = None
    expected_info = read_expected_package_version(workspace)
    if expected_info["status"] == "invalid":
        return {
            "discovery_method": method,
            "found_package_version": version,
            "expected_package_version": None,
            "expected_version_status": "invalid",
            "venv_status": venv_status if venv_status != "blocked" else "blocked",
            "external_env": venv_status == "blocked",
            "blocked_cause": "invalid_expected_package_version",
            "blocked_path": expected_info["path"],
            "decision": DECISION_BLOCKED,
        }
    expected = expected_info["value"]
    version_ok = version is not None and (expected is None or version == expected)
    usable = method != "none" and version_ok and venv_status != "blocked"
    if venv_status == "blocked":
        decision = DECISION_BLOCKED
        blocked_cause = "external_env"
    elif usable:
        decision = DECISION_OK
        blocked_cause = None
    else:
        decision = DECISION_INSTALL_REQUIRED
        blocked_cause = None
    return {
        "discovery_method": method,
        "found_package_version": version,
        "expected_package_version": expected,
        "expected_version_status": expected_info["status"],
        "venv_status": venv_status,
        "external_env": venv_status == "blocked",
        "blocked_cause": blocked_cause,
        "blocked_path": expected_info["path"] if blocked_cause == "invalid_expected_package_version" else None,
        "decision": decision,
    }
