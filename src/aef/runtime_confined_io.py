"""Central confined reads for runtime doctor. Every site is enumerated in RUNTIME_READ_SITES."""

from __future__ import annotations

import zipfile
from pathlib import Path

from .filesystem import is_link_or_reparse_point
from .upgrade_compat import MAX_FILE_BYTES

# Guard: tests assert this set is complete — add a site here when adding a new read path.
RUNTIME_READ_SITES: frozenset[str] = frozenset({
    "agent.runtime_requirements",
    "declared_env.pyvenv_cfg",
    "declared_env.version_file",
    "local_wheel.sha256",
    "checksum.sidecar",
    "dependency_wheel.archive",
})


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


def read_text_confined(workspace: Path, path: Path, *, site: str) -> str | None:
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    if confined_workspace_read_path(workspace, path) is None:
        return None
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_bytes_confined(workspace: Path, path: Path, *, site: str) -> bytes | None:
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    if confined_workspace_read_path(workspace, path) is None:
        return None
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                total += len(chunk)
                if total > MAX_FILE_BYTES:
                    return None
                chunks.append(chunk)
    except OSError:
        return None
    return b"".join(chunks)


def dependency_wheel_is_usable(workspace: Path, path: Path) -> bool:
    """True only for a confined zip wheel exposing wheel metadata."""
    site = "dependency_wheel.archive"
    if site not in RUNTIME_READ_SITES:
        raise ValueError(f"unregistered runtime read site: {site}")
    if confined_workspace_read_path(workspace, path) is None:
        return False
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    has_metadata = any(name.endswith("/METADATA") or name.endswith("/WHEEL") for name in names)
    return has_metadata


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
    try:
        root = workspace.resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return False
    if install_target_occupied(candidate):
        return False
    cursor = candidate
    while True:
        if cursor == root:
            break
        if is_link_or_reparse_point(cursor):
            try:
                resolved = cursor.resolve()
            except OSError:
                return False
            if not workspace_contains(root, resolved):
                return False
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return True
