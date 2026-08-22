"""Central confined reads for runtime doctor. Every site is enumerated in RUNTIME_READ_SITES."""

from __future__ import annotations

import os
import zipfile
from io import BytesIO
from pathlib import Path

from .filesystem import is_link_or_reparse_point
from .upgrade_compat import MAX_FILE_BYTES

# Guard: tests assert this set is complete — add a site before adding a new read path.
RUNTIME_READ_SITES: frozenset[str] = frozenset({
    "agent.runtime_requirements",
    "declared_env.pyvenv_cfg",
    "declared_env.version_file",
    "declared_env.console_script",
    "local_wheel.sha256",
    "checksum.sidecar",
    "dependency_wheel.archive",
})

# Modules scanned by tests/test_runtime_read_coverage.py — no direct Path reads allowed.
RUNTIME_READ_GUARD_MODULES = (
    "runtime_discovery.py",
    "runtime_doctor.py",
)


def workspace_contains(workspace: Path, target: Path) -> bool:
    try:
        root = workspace.resolve()
        resolved = target.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def confined_workspace_read_path(workspace: Path, target: Path) -> Path | None:
    """Reject reads whose resolved target escapes the workspace."""
    try:
        root = workspace.resolve()
        relative = target.relative_to(root)
    except (OSError, ValueError):
        return None
    if not relative.parts or any(part in {"", ".."} for part in relative.parts):
        return None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if is_link_or_reparse_point(cursor):
            try:
                resolved_link = cursor.resolve()
            except OSError:
                return None
            if not workspace_contains(root, resolved_link):
                return None
    try:
        final = target.resolve()
    except OSError:
        return None
    if not workspace_contains(root, final):
        return None
    return final


def open_confined_read_fd(workspace: Path, target: Path) -> int | None:
    """Open the validated resolved path for reading (O_NOFOLLOW when supported)."""
    validated = confined_workspace_read_path(workspace, target)
    if validated is None:
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(validated, flags)
    except OSError:
        return None


def _read_fd_bytes(fd: int, *, max_bytes: int = MAX_FILE_BYTES) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(fd, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
    except OSError:
        return None
    return b"".join(chunks)


def read_text_confined(workspace: Path, path: Path, *, site: str) -> str | None:
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    fd = open_confined_read_fd(workspace, path)
    if fd is None:
        return None
    try:
        payload = _read_fd_bytes(fd)
        if payload is None:
            return None
        return payload.decode("utf-8")
    except UnicodeError:
        return None
    finally:
        os.close(fd)


def read_bytes_confined(workspace: Path, path: Path, *, site: str) -> bytes | None:
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    fd = open_confined_read_fd(workspace, path)
    if fd is None:
        return None
    try:
        return _read_fd_bytes(fd)
    finally:
        os.close(fd)


def confined_file_size(workspace: Path, path: Path, *, site: str) -> int | None:
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    fd = open_confined_read_fd(workspace, path)
    if fd is None:
        return None
    try:
        return os.fstat(fd).st_size
    except OSError:
        return None
    finally:
        os.close(fd)


def _wheel_dist_info_names(names: set[str]) -> list[tuple[str, str]]:
    """Return (dist-info folder, stem) pairs found in a wheel archive."""
    folders: set[str] = set()
    for name in names:
        if ".dist-info/" not in name:
            continue
        folder = name.split("/", 1)[0]
        if folder.endswith(".dist-info"):
            folders.add(folder)
    return [(folder, folder[: -len(".dist-info")]) for folder in sorted(folders)]


def dependency_wheel_is_usable(workspace: Path, path: Path) -> bool:
    """True only for a confined jsonschema wheel with valid top-level wheel metadata."""
    site = "dependency_wheel.archive"
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    fd = open_confined_read_fd(workspace, path)
    if fd is None:
        return False
    try:
        size = os.fstat(fd).st_size
        if size == 0 or size > MAX_FILE_BYTES:
            return False
        payload = _read_fd_bytes(fd, max_bytes=MAX_FILE_BYTES)
        if payload is None:
            return False
    except OSError:
        return False
    finally:
        os.close(fd)
    if not zipfile.is_zipfile(BytesIO(payload)):
        return False
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
            for dist_info, stem in _wheel_dist_info_names(names):
                if not stem.startswith("jsonschema-"):
                    continue
                metadata_name = f"{dist_info}/METADATA"
                wheel_name = f"{dist_info}/WHEEL"
                if metadata_name not in names or wheel_name not in names:
                    continue
                try:
                    metadata = archive.read(metadata_name).decode("utf-8", errors="replace")
                    wheel_meta = archive.read(wheel_name).decode("utf-8", errors="replace")
                except (KeyError, OSError, UnicodeError):
                    continue
                if not metadata.strip() or not wheel_meta.strip():
                    continue
                if "Name: jsonschema" not in metadata:
                    continue
                if "Wheel-Version:" not in wheel_meta:
                    continue
                return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def install_target_occupied(path: Path) -> bool:
    """Return true when a path must not be used as a venv creation target."""
    try:
        if path.exists():
            return True
        if is_link_or_reparse_point(path):
            return True
    except OSError:
        return True
    return False


def proposed_install_path_is_safe(workspace: Path, candidate: Path) -> bool:
    """A not-yet-created install target must stay inside the workspace."""
    if install_target_occupied(candidate):
        return False
    try:
        root = workspace.resolve()
        candidate.relative_to(root)
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return False
    return workspace_contains(root, resolved)
