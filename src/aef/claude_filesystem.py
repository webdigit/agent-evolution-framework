from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from .claude_integration import CLAUDE_BRIDGE_PATH, CORE_DOCTRINE_PATHS
from .filesystem import (
    EvaluationRecoveryRequiredError,
    _evaluation_transaction_entry_present,
)


class ClaudeIntegrationFilesystemError(OSError):
    """Raised when the project-local Claude target cannot be accessed safely."""


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
    target = root.joinpath(*relative.split("/"))
    current = root
    for component in relative.split("/")[:-1]:
        current = current / component
        if current.exists() or _is_link_or_reparse(current):
            if _is_link_or_reparse(current) or not current.is_dir():
                raise ClaudeIntegrationFilesystemError(
                    "unsafe Claude integration path"
                )
    if _is_link_or_reparse(target):
        raise ClaudeIntegrationFilesystemError("unsafe Claude integration target")
    try:
        metadata = target.stat()
    except FileNotFoundError:
        if required:
            raise ClaudeIntegrationFilesystemError("required AEF doctrine is missing")
        return None
    except OSError as exc:
        raise ClaudeIntegrationFilesystemError(
            "Claude integration target cannot be inspected"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ClaudeIntegrationFilesystemError("Claude integration target is not a file")
    try:
        target.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ClaudeIntegrationFilesystemError(
            "Claude integration target escapes the workspace"
        ) from exc
    return target


def validate_claude_doctrine_files(root: str | Path) -> None:
    root = Path(root).resolve()
    for relative in CORE_DOCTRINE_PATHS:
        target = _safe_regular_file(root, relative, required=True)
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ClaudeIntegrationFilesystemError(
                "required AEF doctrine is not readable UTF-8"
            ) from exc
        if not content.strip():
            raise ClaudeIntegrationFilesystemError("required AEF doctrine is empty")


def read_claude_bridge(root: str | Path) -> bytes | None:
    root = Path(root).resolve()
    target = _safe_regular_file(root, CLAUDE_BRIDGE_PATH, required=False)
    if target is None:
        return None
    try:
        raw = target.read_bytes()
        raw.decode("utf-8", "strict")
    except (OSError, UnicodeError) as exc:
        raise ClaudeIntegrationFilesystemError(
            "Claude instruction file is not readable UTF-8"
        ) from exc
    return raw


def claude_bridge_diff(existing: bytes | None, desired: bytes):
    if existing == desired:
        return {"created": [], "modified": [], "removed": []}
    key = "created" if existing is None else "modified"
    return {
        "created": [CLAUDE_BRIDGE_PATH] if key == "created" else [],
        "modified": [CLAUDE_BRIDGE_PATH] if key == "modified" else [],
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
    """Fail closed before an ordinary Claude integration mutation."""
    if _evaluation_transaction_entry_present(root):
        raise EvaluationRecoveryRequiredError(
            "evaluation recovery is required before Claude integration mutation"
        )


def apply_claude_bridge(root: str | Path, existing: bytes | None, desired: bytes):
    """Atomically write the exact pre-rendered bridge and no other path."""
    if not isinstance(desired, bytes):
        raise TypeError("Claude bridge content must be bytes")
    desired.decode("utf-8", "strict")
    root = Path(root).resolve()
    validate_claude_doctrine_files(root)
    actual = read_claude_bridge(root)
    if actual != existing:
        raise ClaudeIntegrationFilesystemError(
            "Claude instruction file changed after preflight"
        )
    diff = claude_bridge_diff(existing, desired)
    if not diff["created"] and not diff["modified"]:
        return diff
    _require_no_evaluation_recovery(root)

    parent = root / ".claude"
    target = parent / "CLAUDE.md"
    descriptor = None
    temporary = None
    parent_created = False
    published = False
    try:
        if parent.exists() or _is_link_or_reparse(parent):
            if _is_link_or_reparse(parent) or not parent.is_dir():
                raise ClaudeIntegrationFilesystemError(
                    "unsafe Claude integration directory"
                )
        _require_no_evaluation_recovery(root)
        if read_claude_bridge(root) != existing:
            raise ClaudeIntegrationFilesystemError(
                "Claude instruction file changed before staging"
            )
        _require_no_evaluation_recovery(root)
        if not parent.exists():
            _require_no_evaluation_recovery(root)
            parent.mkdir()
            parent_created = True
        # Reinspect after optional directory creation and immediately before
        # staging. This narrows the non-hostile TOCTOU window without claiming
        # protection against an adversarial concurrent local process.
        _require_no_evaluation_recovery(root)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".aef-claude-", suffix=".tmp", dir=parent
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
        if read_claude_bridge(root) != existing:
            raise ClaudeIntegrationFilesystemError(
                "Claude instruction file changed before replace"
            )
        os.replace(temporary, target)
        temporary = None
        published = True
        _sync_directory(parent)
    except OSError as exc:
        if isinstance(exc, ClaudeIntegrationFilesystemError):
            raise
        raise ClaudeIntegrationFilesystemError(
            "Claude integration write failed"
        ) from exc
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
                # Never recurse or remove content that appeared during staging.
                pass
    return diff
