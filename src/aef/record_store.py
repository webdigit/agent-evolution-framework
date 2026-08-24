from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .competency_declaration_transaction import (
    declaration_transaction_entry_present,
    declaration_transaction_present,
)
from .filesystem import (
    RECORDS_DIRECTORY,
    CompetencyDeclarationRecoveryRequiredError,
    EvaluationRecoveryRequiredError,
    UpgradeRecoveryRequiredError,
    _evaluation_transaction_entry_present,
    apply_workspace,
    is_link_or_reparse_point,
    load_workspace,
)
from .transaction_guard import evaluation_recovery_required
from .upgrade_transaction import (
    upgrade_transaction_entry_present,
    upgrade_transaction_present,
)
from .record_document import (
    InvalidPersistedRecordError,
    InvalidRecordSubmissionError,
    record_relative_path,
    validate_persisted_record,
)


class InvalidRecordStoreError(ValueError):
    """Raised when RECORD cannot persist safely."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _entry_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _existing_persisted_record(path: Path) -> dict[str, Any] | None:
    """Return a valid existing record, or None when replay must block."""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    try:
        return validate_persisted_record(existing)
    except (InvalidPersistedRecordError, InvalidRecordSubmissionError):
        return None


def persist_record(
    root: str | Path,
    persisted: dict[str, Any],
    *,
    dry_run: bool = False,
) -> tuple[str, str, str]:
    """Persist one aef.record/v1 file. Creates records/ only on first Apply CHANGE."""
    document = validate_persisted_record(persisted)
    relative = record_relative_path(document["record_id"])
    root = Path(root).resolve()
    records_dir = root.joinpath(*RECORDS_DIRECTORY.split("/"))
    target = root.joinpath(*relative.split("/"))

    current = load_workspace(root)
    if upgrade_transaction_present(current) or upgrade_transaction_entry_present(root):
        raise UpgradeRecoveryRequiredError(
            "upgrade recovery is required before workspace mutation"
        )
    if evaluation_recovery_required(current) or _evaluation_transaction_entry_present(root):
        raise EvaluationRecoveryRequiredError(
            "evaluation recovery is required before workspace mutation"
        )
    if declaration_transaction_present(current) or declaration_transaction_entry_present(root):
        raise CompetencyDeclarationRecoveryRequiredError(
            "competency declaration recovery is required before workspace mutation"
        )

    if _entry_exists(records_dir):
        if is_link_or_reparse_point(records_dir) or not records_dir.is_dir():
            raise InvalidRecordStoreError(
                "record_target_unsafe",
                "the records directory is not a regular directory.",
            )
    if _entry_exists(target):
        if is_link_or_reparse_point(target) or not target.is_file():
            raise InvalidRecordStoreError(
                "record_target_unsafe",
                "the existing record path is not a regular file.",
            )
        existing = _existing_persisted_record(target)
        if existing is not None and existing == document:
            return "NO_CHANGE", relative, document["digest"]
        return "BLOCKED", relative, document["digest"]

    if dry_run:
        return "CHANGE", relative, document["digest"]

    desired = deepcopy(current)
    desired.setdefault("files", {})
    desired["files"][relative] = deepcopy(document)
    apply_workspace(root, current, desired)
    return "CHANGE", relative, document["digest"]
