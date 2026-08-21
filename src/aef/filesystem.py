from __future__ import annotations

import json
import os
import stat
import tempfile
import hashlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .strict_json import InvalidStrictJSONError, validate_strict_json


JSON_PATHS = {
    ".agent/manifest.json",
    ".agent/state/migrations.json",
    ".agent/state/decisions.json",
    ".agent/state/career.json",
    ".agent/state/competencies.json",
    ".agent/state/evaluations.json",
    ".agent/integrations/registry.json",
    ".agent/knowledge/knowledge.json",
}
EVALUATION_TRANSACTION_PATH = ".agent/state/evaluation-transaction.json"
UPGRADE_TRANSACTION_PATH = ".agent/state/upgrade-transaction.json"
COMPETENCY_DECLARATION_TRANSACTION_PATH = (
    ".agent/state/competency-declaration-transaction.json"
)
RECORDS_DIRECTORY = ".agent/records"

WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class WorkspacePathError(ValueError):
    """Raised before mutation when a workspace plan contains an unsafe path."""


class EvaluationRecoveryRequiredError(RuntimeError):
    """Raised when ordinary writes encounter an unfinished EVALUATE transaction."""

    code = "evaluation_recovery_required"


class UpgradeRecoveryRequiredError(RuntimeError):
    """Raised when ordinary writes encounter an unfinished UPGRADE transaction."""

    code = "upgrade_recovery_required"


class CompetencyDeclarationRecoveryRequiredError(RuntimeError):
    """Raised when ordinary writes encounter an unfinished declaration transaction."""

    code = "competency_declaration_recovery_required"


TRANSACTION_BUSINESS_PATHS = frozenset({
    ".agent/state/evaluations.json",
    ".agent/state/career.json",
    ".agent/state/competencies.json",
})
TRANSACTION_CAPABILITY_PHASES = {"prepare", "apply", "rollback", "commit", "cleanup"}


@dataclass(frozen=True, slots=True)
class _TransactionWriteCapability:
    """Immutable guard against accidental internal transaction misuse.

    This is not a security boundary against hostile Python code with access to
    private symbols. It binds internal writes to one workspace, validated
    journal identity, transaction, phase, and immutable canonical write plan.
    """

    root: Path
    transaction_id: str
    decision_batch_digest: str
    journal_fingerprint: str
    phase: str
    declared_paths: frozenset[str]
    allowed_paths: frozenset[str]
    expected_created: tuple[tuple[str, str], ...]
    expected_modified: tuple[tuple[str, str], ...]
    expected_removed: tuple[str, ...]


def _journal_fingerprint(journal):
    try:
        validate_strict_json(journal)
        payload = json.dumps(
            journal, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise WorkspacePathError("invalid transaction journal capability") from exc
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _transaction_write_capability(root, journal, phase, *, target_path=None):
    """Create one private capability from an already validated journal."""
    if not isinstance(journal, dict) or phase not in TRANSACTION_CAPABILITY_PHASES:
        raise WorkspacePathError("invalid transaction capability request")
    transaction_id = journal.get("transaction_id")
    digest = journal.get("decision_batch_digest")
    paths = journal.get("paths")
    if (
        not isinstance(transaction_id, str)
        or not isinstance(digest, str)
        or not isinstance(paths, list)
        or not all(isinstance(path, str) for path in paths)
    ):
        raise WorkspacePathError("invalid transaction capability identity")
    declared = frozenset(paths)
    if not declared or not declared.issubset(TRANSACTION_BUSINESS_PATHS):
        raise WorkspacePathError("transaction capability contains forbidden paths")
    entries = journal.get("files")
    if (
        not isinstance(entries, list)
        or not all(isinstance(entry, dict) for entry in entries)
        or [entry.get("path") for entry in entries] != paths
        or not all(
            isinstance(entry.get("before_content"), str)
            and isinstance(entry.get("after_content"), str)
            for entry in entries
        )
    ):
        raise WorkspacePathError("invalid transaction capability plan")
    content_by_path = {entry["path"]: entry for entry in entries}
    expected_created = ()
    expected_modified = ()
    expected_removed = ()
    if phase == "apply":
        if target_path not in declared:
            raise WorkspacePathError("transaction apply path was not declared")
        allowed = frozenset({target_path})
        expected_modified = ((target_path, content_by_path[target_path]["after_content"]),)
    elif target_path is not None:
        raise WorkspacePathError("transaction target is invalid for this phase")
    elif phase == "rollback":
        allowed = declared | {EVALUATION_TRANSACTION_PATH}
        expected_modified = tuple(sorted(
            (path, content_by_path[path]["before_content"]) for path in declared
        ))
        expected_removed = (EVALUATION_TRANSACTION_PATH,)
    else:
        allowed = frozenset({EVALUATION_TRANSACTION_PATH})
        if phase == "prepare":
            expected_created = ((EVALUATION_TRANSACTION_PATH, _serialize(
                EVALUATION_TRANSACTION_PATH, journal
            )),)
        elif phase == "commit":
            committed = deepcopy(journal)
            committed["phase"] = "committed"
            expected_modified = ((EVALUATION_TRANSACTION_PATH, _serialize(
                EVALUATION_TRANSACTION_PATH, committed
            )),)
        else:
            expected_removed = (EVALUATION_TRANSACTION_PATH,)
    return _TransactionWriteCapability(
        root=Path(root).resolve(),
        transaction_id=transaction_id,
        decision_batch_digest=digest,
        journal_fingerprint=_journal_fingerprint(journal),
        phase=phase,
        declared_paths=declared,
        allowed_paths=allowed,
        expected_created=expected_created,
        expected_modified=expected_modified,
        expected_removed=expected_removed,
    )


def _disk_journal(root):
    path = root.joinpath(*EVALUATION_TRANSACTION_PATH.split("/"))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspacePathError("transaction journal identity cannot be verified") from exc


def _evaluation_transaction_entry_present(root: Path) -> bool:
    """Inspect the reserved entry fail-closed, including broken links.

    A definite ``FileNotFoundError`` is the only state treated as absence.
    Files, directories, symlinks, junctions, reparse points, permission errors,
    and indeterminate filesystem failures all prevent ordinary mutation.
    """
    path = root.joinpath(*EVALUATION_TRANSACTION_PATH.split("/"))
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise EvaluationRecoveryRequiredError(
            "evaluation transaction entry cannot be inspected safely"
        ) from None
    return True


def _upgrade_transaction_entry_present(root: Path) -> bool:
    path = root.joinpath(*UPGRADE_TRANSACTION_PATH.split("/"))
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _competency_declaration_transaction_entry_present(root: Path) -> bool:
    path = root.joinpath(*COMPETENCY_DECLARATION_TRANSACTION_PATH.split("/"))
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def load_workspace(root: str | Path) -> dict[str, Any]:
    """
    Read the workspace into the in-memory project representation used by AEF.

    Only .agent/** is considered framework state here. Other project files are
    intentionally left untouched by this adapter.
    """
    root = Path(root).resolve()
    project: dict[str, Any] = {"files": {}, "decisions": {"decisions": []}}

    agent_dir = root / ".agent"
    if not agent_dir.exists():
        return project

    for path in sorted(p for p in agent_dir.rglob("*") if p.is_file()):
        rel = _relative_posix(path, root)
        raw = path.read_text(encoding="utf-8")

        if rel in JSON_PATHS or path.suffix.lower() == ".json":
            try:
                project["files"][rel] = json.loads(raw)
            except json.JSONDecodeError:
                # Preserve malformed/unexpected JSON as text rather than
                # silently destroying it. AUDIT can report it later.
                project["files"][rel] = raw
        else:
            project["files"][rel] = raw

    decisions_path = ".agent/state/decisions.json"
    stored_decisions = project["files"].get(decisions_path)
    if isinstance(stored_decisions, dict) and "decisions" in stored_decisions:
        project["decisions"] = deepcopy(stored_decisions)

    return project


def _serialize(rel_path: str, value: Any) -> str:
    if rel_path.endswith(".json") or isinstance(value, (dict, list)):
        try:
            validate_strict_json(value)
        except InvalidStrictJSONError as exc:
            # Keep the filesystem serializer's established public exception
            # categories while enforcing the stricter preflight contract.
            if exc.reason in {"non_string_key", "unsupported_value"}:
                raise TypeError(str(exc)) from None
            if exc.reason == "non_finite_number":
                raise ValueError("Out of range float values are not JSON compliant") from None
            if exc.reason == "cyclic_reference":
                raise ValueError("Circular reference detected") from None
            raise
        return json.dumps(
            value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False
        ) + "\n"
    if isinstance(value, str):
        return value
    return str(value)


def is_link_or_reparse_point(path: Path) -> bool:
    """Return true for symlinks, Windows junctions, and other reparse points."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False

    if stat.S_ISLNK(metadata.st_mode):
        return True
    if getattr(path, "is_junction", lambda: False)():
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


_is_link_or_reparse_point = is_link_or_reparse_point


def validate_workspace_rel_path(rel_path: str) -> tuple[str, ...]:
    """Validate one canonical POSIX relative path confined below ``.agent/``.

    Syntax only: no filesystem access. Returns the POSIX parts.
    """
    if not isinstance(rel_path, str) or not rel_path:
        raise WorkspacePathError("workspace paths must be non-empty strings")
    if "\\" in rel_path:
        raise WorkspacePathError(f"workspace path must use POSIX separators: {rel_path!r}")

    posix_path = PurePosixPath(rel_path)
    windows_path = PureWindowsPath(rel_path)
    parts = rel_path.split("/")
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise WorkspacePathError(f"absolute workspace path is forbidden: {rel_path!r}")
    if any(part in {"", ".", ".."} for part in parts):
        raise WorkspacePathError(f"non-canonical workspace path is forbidden: {rel_path!r}")
    if len(parts) < 2 or parts[0] != ".agent":
        raise WorkspacePathError(f"workspace writes must target a file below .agent/: {rel_path!r}")
    for part in parts:
        if ":" in part:
            raise WorkspacePathError(f"Windows alternate data streams are forbidden: {rel_path!r}")
        if part.endswith((".", " ")):
            raise WorkspacePathError(f"Windows-trimmed path components are forbidden: {rel_path!r}")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise WorkspacePathError(f"Windows reserved path component is forbidden: {rel_path!r}")
    return tuple(parts)


def _validate_workspace_path(root: Path, rel_path: str) -> Path:
    """Validate one canonical POSIX file path confined below ``.agent/``."""
    parts = validate_workspace_rel_path(rel_path)
    target = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if is_link_or_reparse_point(cursor):
            raise WorkspacePathError(f"workspace path crosses a link or reparse point: {rel_path!r}")

    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(f"workspace path escapes its root: {rel_path!r}") from exc
    if resolved_target == root:
        raise WorkspacePathError(f"workspace path must target a file: {rel_path!r}")
    return target


def _validate_workspace_plan(root: Path, diff: dict[str, list[str]]) -> dict[str, Path]:
    """Validate the complete diff before the first filesystem mutation."""
    planned_paths = {
        rel_path
        for category in ("created", "modified", "removed")
        for rel_path in diff[category]
    }
    return {
        rel_path: _validate_workspace_path(root, rel_path)
        for rel_path in sorted(planned_paths)
    }


def _sync_parent_directory(directory: Path) -> None:
    """Best-effort POSIX directory sync after an atomic replacement."""
    if os.name != "posix":
        return
    descriptor = -1
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    except OSError:
        # Some filesystems do not support directory fsync. os.replace() still
        # provides observable per-file atomicity, with weaker crash durability.
        pass
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                # The replacement already succeeded. Directory close is part
                # of the best-effort durability step and must not report a
                # false write failure.
                pass


def _atomic_write(root: Path, rel_path: str, target: Path, content: str) -> None:
    """Atomically replace one file using a temporary sibling.

    This is atomic per file only. It is not a transaction across the complete
    ``.agent`` tree; multi-file journaling and rollback belong to UPGRADE.

    Revalidating immediately before staging and replacement narrows a TOCTOU
    window. AEF V1 assumes no hostile local process concurrently rewrites the
    workspace and does not use platform-specific handle-relative APIs.
    """
    if _validate_workspace_path(root, rel_path) != target:
        raise WorkspacePathError(f"workspace target changed before staging: {rel_path!r}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        if target.exists():
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        if _validate_workspace_path(root, rel_path) != target:
            raise WorkspacePathError(f"workspace target changed before replacement: {rel_path!r}")
        os.replace(temporary, target)
        _sync_parent_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def compute_diff(current: dict[str, Any], desired: dict[str, Any]) -> dict[str, list[str]]:
    current_files = current.get("files", {})
    desired_files = desired.get("files", {})

    created = sorted(set(desired_files) - set(current_files))
    removed = sorted(set(current_files) - set(desired_files))
    modified = sorted(
        path
        for path in (set(current_files) & set(desired_files))
        if current_files[path] != desired_files[path]
    )

    return {
        "created": created,
        "modified": modified,
        "removed": removed,
    }


def _prepare_workspace_state(desired: dict[str, Any]) -> dict[str, Any]:
    """Return the exact state the filesystem adapter would persist."""
    prepared = deepcopy(desired)
    prepared.setdefault("files", {})
    decisions = prepared.get("decisions")
    if isinstance(decisions, dict) and decisions.get("decisions"):
        prepared["files"][".agent/state/decisions.json"] = deepcopy(decisions)
    return prepared


def plan_workspace(current: dict[str, Any], desired: dict[str, Any]) -> dict[str, list[str]]:
    """Compute the complete persistence diff without mutating the filesystem."""
    return compute_diff(current, _prepare_workspace_state(desired))


def render_workspace_plan(
    current: dict[str, Any], desired: dict[str, Any]
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Render exactly the changed file contents used by ``apply_workspace``."""
    prepared = _prepare_workspace_state(desired)
    diff = compute_diff(current, prepared)
    serialized = {
        rel_path: _serialize(rel_path, prepared["files"][rel_path])
        for rel_path in diff["created"] + diff["modified"]
    }
    return diff, serialized


def _apply_workspace_unchecked(
    root: str | Path,
    current: dict[str, Any],
    desired: dict[str, Any],
    *,
    allow_delete: bool = False,
) -> dict[str, list[str]]:
    root = Path(root).resolve()

    desired = _prepare_workspace_state(desired)

    diff, serialized = render_workspace_plan(current, desired)
    targets = _validate_workspace_plan(root, diff)

    for rel_path in diff["created"] + diff["modified"]:
        target = targets[rel_path]
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(root, rel_path, target, serialized[rel_path])

    if allow_delete:
        for rel_path in diff["removed"]:
            target = targets[rel_path]
            if target.exists() and target.is_file():
                target.unlink()

    return diff


def _apply_workspace_transaction(
    root: str | Path,
    current: dict[str, Any],
    desired: dict[str, Any],
    capability: _TransactionWriteCapability,
    *,
    allow_delete: bool = False,
) -> dict[str, list[str]]:
    """Private transaction writer restricted to one validated path capability."""
    if not isinstance(capability, _TransactionWriteCapability):
        raise WorkspacePathError("invalid transaction write capability")
    root = Path(root).resolve()
    if root != capability.root:
        raise WorkspacePathError("transaction capability belongs to another workspace")
    current_journal = current.get("files", {}).get(EVALUATION_TRANSACTION_PATH)
    desired_journal = desired.get("files", {}).get(EVALUATION_TRANSACTION_PATH)
    if capability.phase == "prepare":
        if current_journal is not None or desired_journal is None:
            raise WorkspacePathError("invalid transaction prepare state")
        if _journal_fingerprint(desired_journal) != capability.journal_fingerprint:
            raise WorkspacePathError("transaction prepare journal changed")
        if _evaluation_transaction_entry_present(root):
            raise WorkspacePathError("transaction journal already exists")
    else:
        if current_journal is None:
            raise WorkspacePathError("transaction journal is missing")
        if (
            current_journal.get("transaction_id") != capability.transaction_id
            or current_journal.get("decision_batch_digest")
            != capability.decision_batch_digest
            or _journal_fingerprint(current_journal) != capability.journal_fingerprint
        ):
            raise WorkspacePathError("transaction capability identity mismatch")
        real_journal = _disk_journal(root)
        if _journal_fingerprint(real_journal) != capability.journal_fingerprint:
            raise WorkspacePathError("workspace transaction journal changed")
        if capability.phase == "apply" and desired_journal != current_journal:
            raise WorkspacePathError("transaction apply cannot change the journal")
        if capability.phase == "rollback" and desired_journal is not None:
            raise WorkspacePathError("transaction rollback must remove the journal")
        if capability.phase == "cleanup" and desired_journal is not None:
            raise WorkspacePathError("transaction cleanup must remove the journal")
        if capability.phase == "commit":
            expected = deepcopy(current_journal)
            expected["phase"] = "committed"
            if desired_journal != expected:
                raise WorkspacePathError("transaction commit journal is invalid")
    prepared = _prepare_workspace_state(desired)
    diff, serialized = render_workspace_plan(current, prepared)
    actual_created = tuple(sorted(
        (path, serialized[path]) for path in diff["created"]
    ))
    actual_modified = tuple(sorted(
        (path, serialized[path]) for path in diff["modified"]
    ))
    actual_removed = tuple(sorted(diff["removed"])) if allow_delete else ()
    if diff["removed"] and not allow_delete:
        raise WorkspacePathError("transaction plan contains forbidden removals")

    if capability.phase == "rollback":
        expected_before = dict(capability.expected_modified)
        desired_files = prepared.get("files", {})
        try:
            final_business = tuple(sorted(
                (path, _serialize(path, desired_files[path]))
                for path in capability.declared_paths
            ))
        except KeyError as exc:
            raise WorkspacePathError("transaction rollback plan is incomplete") from exc
        if (
            actual_created
            or actual_removed != capability.expected_removed
            or final_business != capability.expected_modified
            or any(expected_before.get(path) != content for path, content in actual_modified)
        ):
            raise WorkspacePathError("transaction plan differs from capability")
    elif (
        actual_created != capability.expected_created
        or actual_modified != capability.expected_modified
        or actual_removed != capability.expected_removed
    ):
        raise WorkspacePathError("transaction plan differs from capability")

    mutation_paths = {
        path for path, _ in actual_created + actual_modified
    } | set(actual_removed)
    if not mutation_paths.issubset(capability.allowed_paths):
        raise WorkspacePathError("transaction write exceeds declared capability")
    return _apply_workspace_unchecked(
        root, current, desired, allow_delete=allow_delete
    )


def apply_workspace(
    root: str | Path,
    current: dict[str, Any],
    desired: dict[str, Any],
    *,
    allow_delete: bool = False,
) -> dict[str, list[str]]:
    """Apply an ordinary minimal diff unless EVALUATE or UPGRADE recovery is required.

    The guard consults both the supplied snapshot and the real workspace so a
    stale or incomplete ``current`` object cannot bypass an unfinished
    transaction. Transaction recovery uses a private path-scoped writer.
    """
    root = Path(root).resolve()
    prepared = _prepare_workspace_state(desired)
    diff = compute_diff(current, prepared)
    mutation_paths = set(diff["created"] + diff["modified"])
    if allow_delete:
        mutation_paths.update(diff["removed"])
    if not mutation_paths:
        return diff
    current_files = current.get("files", {}) if isinstance(current, dict) else {}
    if EVALUATION_TRANSACTION_PATH in current_files:
        raise EvaluationRecoveryRequiredError(
            "evaluation recovery is required before workspace mutation"
        )
    if _evaluation_transaction_entry_present(root):
        raise EvaluationRecoveryRequiredError(
            "evaluation recovery is required before workspace mutation"
        )
    # Reinspect immediately before entering the ordinary writer. This narrows
    # the non-hostile TOCTOU window without claiming native handle-relative
    # protection in the V1 threat model.
    if _evaluation_transaction_entry_present(root):
        raise EvaluationRecoveryRequiredError(
            "evaluation recovery is required before workspace mutation"
        )
    if UPGRADE_TRANSACTION_PATH in current_files:
        raise UpgradeRecoveryRequiredError(
            "upgrade recovery is required before workspace mutation"
        )
    if _upgrade_transaction_entry_present(root):
        raise UpgradeRecoveryRequiredError(
            "upgrade recovery is required before workspace mutation"
        )
    if COMPETENCY_DECLARATION_TRANSACTION_PATH in current_files:
        raise CompetencyDeclarationRecoveryRequiredError(
            "competency declaration recovery is required before workspace mutation"
        )
    if _competency_declaration_transaction_entry_present(root):
        raise CompetencyDeclarationRecoveryRequiredError(
            "competency declaration recovery is required before workspace mutation"
        )
    return _apply_workspace_unchecked(
        root, current, desired, allow_delete=allow_delete
    )


def snapshot_workspace(root: str | Path) -> dict[str, Any]:
    """Canonical reload used for replay/idempotence assertions."""
    return load_workspace(root)
