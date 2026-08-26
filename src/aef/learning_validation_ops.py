"""Plan / apply learning validation (voie B)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .competency_declaration_transaction import (
    declaration_transaction_entry_present,
    declaration_transaction_present,
)
from .filesystem import (
    _evaluation_transaction_entry_present,
    apply_workspace,
    is_link_or_reparse_point,
    load_workspace,
    plan_workspace,
    workspace_mutation_lock,
)
from .ingest_ops import KNOWLEDGE_PATH, MANIFEST_PATH, RECORDS_DIRECTORY
from .learning_validation import (
    LearningValidationBlockedError,
    bind_validation_records,
    resolve_validation_outcome,
    validate_learning_validation,
)
from .record_document import (
    InvalidPersistedRecordError,
    InvalidRecordSubmissionError,
    record_relative_path,
    validate_persisted_record,
)
from .schema_validation import validate_persisted_knowledge
from .transaction_guard import evaluation_recovery_required
from .upgrade_transaction import (
    upgrade_transaction_entry_present,
    upgrade_transaction_present,
)


def _blocked(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise LearningValidationBlockedError(code, message, details)


def _require_initialized(current: dict[str, Any]) -> dict[str, Any]:
    files = current.get("files") if isinstance(current, dict) else None
    if not isinstance(files, dict):
        _blocked("workspace_not_initialized", "the workspace is not an initialized AEF project.")
    if MANIFEST_PATH not in files or KNOWLEDGE_PATH not in files:
        _blocked("workspace_not_initialized", "the workspace is not an initialized AEF project.")
    knowledge = files[KNOWLEDGE_PATH]
    if not isinstance(knowledge, dict):
        _blocked("invalid_knowledge_state", "persisted knowledge is not a JSON object.")
    return knowledge


def _guard_transactions(root: Path, current: dict[str, Any]) -> None:
    if upgrade_transaction_present(current) or upgrade_transaction_entry_present(root):
        _blocked(
            "upgrade_recovery_required",
            "upgrade recovery is required before workspace mutation",
        )
    if evaluation_recovery_required(current) or _evaluation_transaction_entry_present(root):
        _blocked(
            "evaluation_recovery_required",
            "evaluation recovery is required before workspace mutation",
        )
    if declaration_transaction_present(current) or declaration_transaction_entry_present(root):
        _blocked(
            "competency_declaration_recovery_required",
            "competency declaration recovery is required before workspace mutation",
        )


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _load_persisted_records(
    root: Path,
    current: dict[str, Any],
    record_ids: list[str],
) -> dict[str, Any]:
    records_dir = root.joinpath(*RECORDS_DIRECTORY.split("/"))
    if records_dir.exists() and is_link_or_reparse_point(records_dir):
        _blocked(
            "record_target_unsafe",
            "the records directory is not a regular directory.",
        )
    persisted: dict[str, Any] = {}
    files = current.get("files", {})
    for record_id in record_ids:
        relative = record_relative_path(record_id)
        target = root.joinpath(*relative.split("/"))
        if _entry_exists(target) and is_link_or_reparse_point(target):
            _blocked(
                "record_target_unsafe",
                "a cited record path is not a regular file.",
                {"record_id": record_id},
            )
        stored = files.get(relative)
        if stored is None:
            _blocked(
                "record_missing",
                "a cited record is not persisted.",
                {"record_id": record_id},
            )
        if not isinstance(stored, dict):
            _blocked(
                "record_unreadable",
                "a cited record is not a readable aef.record/v1 document.",
                {"record_id": record_id},
            )
        try:
            persisted[record_id] = validate_persisted_record(stored)
        except (InvalidPersistedRecordError, InvalidRecordSubmissionError) as exc:
            _blocked(
                "record_unreadable",
                "a cited record is not a readable aef.record/v1 document.",
                {"record_id": record_id, "cause": exc.code},
            )
    return persisted


def plan_validate(
    root: str | Path,
    document: Any,
    *,
    dry_run: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    """Validate document and project knowledge. Write only when dry_run is false."""
    validation = validate_learning_validation(document)
    root = Path(root).resolve()

    with workspace_mutation_lock(root):
        current = load_workspace(root)
        preview_files = current.get("files") if isinstance(current, dict) else None
        if (
            not isinstance(preview_files, dict)
            or MANIFEST_PATH not in preview_files
            or KNOWLEDGE_PATH not in preview_files
        ):
            _require_initialized(current)
        _guard_transactions(root, current)
        knowledge = deepcopy(_require_initialized(current))
        record_ids = [
            citation["record_id"] for citation in validation.get("records") or []
        ]
        if record_ids:
            persisted = _load_persisted_records(root, current, record_ids)
            bind_validation_records(validation, persisted)

        status, next_state, validated = resolve_validation_outcome(validation, knowledge)
        try:
            validate_persisted_knowledge(next_state)
        except Exception as exc:
            raise LearningValidationBlockedError(
                "invalid_knowledge_state",
                "projected knowledge is not a valid persisted knowledge document.",
            ) from exc

        desired = deepcopy(current)
        desired.setdefault("files", {})
        desired["files"][KNOWLEDGE_PATH] = deepcopy(next_state)
        overall = "NO_CHANGE" if next_state == knowledge else status
        result = {
            "hypotheses": validation.get("hypotheses") or [],
            "validated": validated,
            "knowledge_path": KNOWLEDGE_PATH,
            "human_action_required": False,
        }
        meta: dict[str, Any] = {}
        if overall == "NO_CHANGE" or dry_run:
            diff = (
                {"created": [], "modified": [], "removed": []}
                if overall == "NO_CHANGE"
                else plan_workspace(current, desired)
            )
            return overall, result, meta, diff
        return overall, result, meta, apply_workspace(root, current, desired)
