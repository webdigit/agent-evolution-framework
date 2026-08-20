"""UPGRADE journal — distinct protocol from EVALUATE."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .filesystem import WorkspacePathError, validate_workspace_rel_path
from .strict_json import InvalidStrictJSONError, validate_strict_json
from .upgrade_compat import UPGRADE_TRANSACTION_PATH


TRANSACTION_PROTOCOL = "aef.upgrade-transaction/v1"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TRANSACTION_FIELDS = {
    "protocol",
    "transaction_id",
    "workspace_instance_id",
    "from_schema_version",
    "to_schema_version",
    "phase",
    "migration_ids",
    "paths",
    "created_paths",
    "files",
}
FILE_FIELDS = {
    "path", "before_hash", "after_hash", "before_content", "after_content",
}


class InvalidUpgradeTransactionError(ValueError):
    """Raised when the recoverable UPGRADE journal is invalid."""


def _canonical_json(value: Any) -> str:
    try:
        validate_strict_json(value)
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction JSON") from exc


def _filesystem_json(value: Any) -> str:
    try:
        validate_strict_json(value)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        ) + "\n"
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction content") from exc


def sha256_text(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def filesystem_json(value: Any) -> str:
    return _filesystem_json(value)


def upgrade_transaction_present(project: dict[str, Any] | None) -> bool:
    files = project.get("files") if isinstance(project, dict) else None
    return isinstance(files, dict) and UPGRADE_TRANSACTION_PATH in files


def upgrade_transaction_entry_present(root: str | Path) -> bool:
    path = Path(root).resolve().joinpath(*UPGRADE_TRANSACTION_PATH.split("/"))
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def validate_upgrade_transaction(journal: Any) -> dict[str, Any]:
    try:
        validate_strict_json(journal)
    except InvalidStrictJSONError as exc:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction") from exc
    if not isinstance(journal, dict) or set(journal) != TRANSACTION_FIELDS:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction")
    if journal["protocol"] != TRANSACTION_PROTOCOL:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction protocol")
    if journal["phase"] not in {"prepared", "committed"}:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction phase")
    if not isinstance(journal["workspace_instance_id"], str) or not journal["workspace_instance_id"]:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction identity")
    if not SHA256_PATTERN.fullmatch(journal["transaction_id"]):
        raise InvalidUpgradeTransactionError("invalid upgrade transaction identity")
    for key in ("from_schema_version", "to_schema_version"):
        if not isinstance(journal[key], str) or not journal[key]:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction version")
    if not isinstance(journal["migration_ids"], list) or not all(
        isinstance(item, str) and item for item in journal["migration_ids"]
    ):
        raise InvalidUpgradeTransactionError("invalid upgrade transaction migrations")
    paths = journal["paths"]
    created = journal["created_paths"]
    files = journal["files"]
    if not isinstance(paths, list) or not isinstance(created, list) or not isinstance(files, list):
        raise InvalidUpgradeTransactionError("invalid upgrade transaction plan")
    if not all(isinstance(path, str) for path in paths + created):
        raise InvalidUpgradeTransactionError("invalid upgrade transaction path")
    if paths != sorted(set(paths)) or created != sorted(set(created)):
        raise InvalidUpgradeTransactionError("invalid upgrade transaction paths")
    if not set(created).issubset(set(paths)):
        raise InvalidUpgradeTransactionError("invalid upgrade transaction created paths")
    _validate_journal_paths(paths)
    file_paths = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != FILE_FIELDS:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction file")
        if entry["path"] not in paths:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction file path")
        if not all(
            isinstance(entry[field], str)
            for field in ("before_hash", "after_hash", "before_content", "after_content")
        ):
            raise InvalidUpgradeTransactionError("invalid upgrade transaction content")
        if not SHA256_PATTERN.fullmatch(entry["before_hash"]) or not SHA256_PATTERN.fullmatch(
            entry["after_hash"]
        ):
            raise InvalidUpgradeTransactionError("invalid upgrade transaction hash")
        if sha256_text(entry["before_content"]) != entry["before_hash"]:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction hash")
        if sha256_text(entry["after_content"]) != entry["after_hash"]:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction hash")
        file_paths.append(entry["path"])
    if file_paths != paths:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction files")
    expected_id = compute_transaction_id(journal)
    if journal["transaction_id"] != expected_id:
        raise InvalidUpgradeTransactionError("invalid upgrade transaction identity")
    return journal


def _validate_journal_paths(paths: list[str]) -> None:
    folded: dict[str, str] = {}
    journal_key = UPGRADE_TRANSACTION_PATH.casefold()
    for path in paths:
        try:
            validate_workspace_rel_path(path)
        except WorkspacePathError as exc:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction path") from exc
        key = path.casefold()
        if key == journal_key:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction path")
        previous = folded.get(key)
        if previous is not None and previous != path:
            raise InvalidUpgradeTransactionError("invalid upgrade transaction paths")
        folded[key] = path


def compute_transaction_id(journal: dict[str, Any]) -> str:
    """Identity excludes ledger bytes so the id can be stored inside the ledger."""
    from .upgrade_compat import LEDGER_PATH
    payload = {
        "created_paths": journal.get("created_paths"),
        "from_schema_version": journal.get("from_schema_version"),
        "migration_ids": journal.get("migration_ids"),
        "paths": journal.get("paths"),
        "to_schema_version": journal.get("to_schema_version"),
        "workspace_instance_id": journal.get("workspace_instance_id"),
        "files": [
            {
                "after_hash": entry.get("after_hash"),
                "before_hash": entry.get("before_hash"),
                "path": entry.get("path"),
            }
            for entry in journal.get("files", [])
            if entry.get("path") != LEDGER_PATH
        ],
    }
    return sha256_text(_canonical_json(payload))


def build_upgrade_transaction(
    *,
    workspace_instance_id: str,
    from_schema_version: str,
    to_schema_version: str,
    migration_ids: list[str],
    files: list[dict[str, str]],
    created_paths: list[str],
    phase: str = "prepared",
) -> dict[str, Any]:
    paths = [entry["path"] for entry in files]
    body = {
        "protocol": TRANSACTION_PROTOCOL,
        "workspace_instance_id": workspace_instance_id,
        "from_schema_version": from_schema_version,
        "to_schema_version": to_schema_version,
        "phase": phase,
        "migration_ids": list(migration_ids),
        "paths": paths,
        "created_paths": sorted(created_paths),
        "files": files,
    }
    body["transaction_id"] = compute_transaction_id(body)
    return validate_upgrade_transaction(body)


def serialize_file_entry(path: str, before: Any, after: Any, *, created: bool) -> dict[str, str]:
    if created:
        before_content = ""
    elif isinstance(before, str):
        before_content = before
    else:
        before_content = _filesystem_json(before)
    after_content = after if isinstance(after, str) else _filesystem_json(after)
    return {
        "path": path,
        "before_hash": sha256_text(before_content),
        "after_hash": sha256_text(after_content),
        "before_content": before_content,
        "after_content": after_content,
    }
