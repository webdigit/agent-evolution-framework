"""Atomic read/write for root-level guidance doors (AGENTS.md, CLAUDE.md, GEMINI.md)."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from .claude_integration import CORE_DOCTRINE_PATHS
from .claude_filesystem import ClaudeIntegrationFilesystemError, validate_claude_doctrine_files
from .filesystem import (
    EvaluationRecoveryRequiredError,
    _evaluation_transaction_entry_present,
    copy_file_mode,
    file_is_readonly,
)
from .guidance_integration import DOOR_SPECS


class GuidanceFilesystemError(ClaudeIntegrationFilesystemError):
    """Raised when a guidance door path cannot be accessed safely."""


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    if getattr(path, "is_junction", lambda: False)():
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & flag)


def _safe_regular_file(root: Path, relative: str, *, required: bool) -> Path | None:
    if "/" in relative or "\\" in relative or relative in {".", ".."}:
        # Root-level doors only (no nested paths except validated relative).
        if relative.startswith(".claude/"):
            pass
        elif "/" in relative or "\\" in relative:
            raise GuidanceFilesystemError("unsafe guidance path")
    target = root.joinpath(*relative.split("/"))
    current = root
    parts = relative.split("/")
    for component in parts[:-1]:
        current = current / component
        if current.exists() or _is_link_or_reparse(current):
            if _is_link_or_reparse(current) or not current.is_dir():
                raise GuidanceFilesystemError("unsafe guidance path")
    if _is_link_or_reparse(target):
        raise GuidanceFilesystemError("unsafe guidance target")
    try:
        metadata = target.stat()
    except FileNotFoundError:
        if required:
            raise GuidanceFilesystemError("required guidance target is missing")
        return None
    except OSError as exc:
        raise GuidanceFilesystemError("guidance target cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise GuidanceFilesystemError("guidance target is not a file")
    try:
        target.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise GuidanceFilesystemError("guidance target escapes the workspace") from exc
    return target


def read_guidance_file(root: str | Path, relative: str) -> bytes | None:
    root = Path(root).resolve()
    target = _safe_regular_file(root, relative, required=False)
    if target is None:
        return None
    try:
        raw = target.read_bytes()
        raw.decode("utf-8", "strict")
    except (OSError, UnicodeError) as exc:
        raise GuidanceFilesystemError("guidance file is not readable UTF-8") from exc
    return raw


def guidance_diff(relative: str, existing: bytes | None, desired: bytes):
    if existing == desired:
        return {"created": [], "modified": [], "removed": []}
    key = "created" if existing is None else "modified"
    return {
        "created": [relative] if key == "created" else [],
        "modified": [relative] if key == "modified" else [],
        "removed": [],
    }


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _require_no_evaluation_recovery(root: Path) -> None:
    if _evaluation_transaction_entry_present(root):
        raise EvaluationRecoveryRequiredError(
            "evaluation recovery is required before guidance integration mutation"
        )


def apply_guidance_file(
    root: str | Path,
    relative: str,
    existing: bytes | None,
    desired: bytes,
):
    """Atomically write one pre-rendered guidance file and no other path."""
    if not isinstance(desired, bytes):
        raise TypeError("guidance content must be bytes")
    desired.decode("utf-8", "strict")
    root = Path(root).resolve()
    validate_claude_doctrine_files(root)
    actual = read_guidance_file(root, relative)
    if actual != existing:
        raise GuidanceFilesystemError("guidance file changed after preflight")
    diff = guidance_diff(relative, existing, desired)
    if not diff["created"] and not diff["modified"]:
        return diff
    _require_no_evaluation_recovery(root)

    parts = relative.split("/")
    parent = root.joinpath(*parts[:-1]) if len(parts) > 1 else root
    target = root.joinpath(*parts)
    descriptor = None
    temporary = None
    parent_created = False
    published = False
    try:
        if parent != root:
            if parent.exists() or _is_link_or_reparse(parent):
                if _is_link_or_reparse(parent) or not parent.is_dir():
                    raise GuidanceFilesystemError("unsafe guidance directory")
            _require_no_evaluation_recovery(root)
            if read_guidance_file(root, relative) != existing:
                raise GuidanceFilesystemError("guidance file changed before staging")
            _require_no_evaluation_recovery(root)
            if not parent.exists():
                _require_no_evaluation_recovery(root)
                parent.mkdir(parents=False)
                parent_created = True
        else:
            _require_no_evaluation_recovery(root)
            if read_guidance_file(root, relative) != existing:
                raise GuidanceFilesystemError("guidance file changed before staging")
        _require_no_evaluation_recovery(root)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".aef-guidance-", suffix=".tmp", dir=parent,
        )
        temporary = Path(temporary_name)
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        stream_error = None
        try:
            stream.write(desired)
            stream.flush()
            os.fsync(stream.fileno())
        except Exception as exc:
            stream_error = exc
            raise
        finally:
            try:
                stream.close()
            except OSError:
                if stream_error is None:
                    raise
        if read_guidance_file(root, relative) != existing:
            raise GuidanceFilesystemError("guidance file changed before replace")
        if target.exists() and file_is_readonly(target):
            raise GuidanceFilesystemError("guidance file is not replaceable")
        if target.exists():
            copy_file_mode(target, temporary)
        os.replace(temporary, target)
        temporary = None
        published = True
        _sync_directory(parent)
    except OSError as exc:
        if isinstance(exc, GuidanceFilesystemError):
            raise
        raise GuidanceFilesystemError("guidance write failed") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if parent_created and not published:
            try:
                parent.rmdir()
            except OSError:
                pass
    return diff


def door_path(door: str) -> str:
    return DOOR_SPECS[door]["path"]


def ensure_doctrine_present(root: str | Path) -> None:
    validate_claude_doctrine_files(root)
    # Touch CORE_DOCTRINE_PATHS import for static analysis / packing.
    assert CORE_DOCTRINE_PATHS
