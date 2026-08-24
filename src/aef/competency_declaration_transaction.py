"""Competency declaration crash-recovery journal — distinct from EVALUATE / UPGRADE."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .strict_json import InvalidStrictJSONError, validate_strict_json


TRANSACTION_PATH = ".agent/state/competency-declaration-transaction.json"
TRANSACTION_PROTOCOL = "aef.competency-declare-transaction/v1"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TRANSACTION_FIELDS = {
    "protocol",
    "transaction_id",
    "declaration_digest",
    "phase",
    "paths",
    "created_paths",
    "files",
}
FILE_FIELDS = {
    "path", "before_hash", "after_hash", "before_content", "after_content",
}
ALLOWED_BUSINESS_PATHS = frozenset({
    ".agent/state/competencies.json",
    ".agent/state/competency-declarations.json",
})


class InvalidCompetencyDeclarationTransactionError(ValueError):
    """Raised when the recoverable declaration journal is invalid."""


def _canonical_json(value: Any) -> str:
    try:
        validate_strict_json(value)
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction JSON"
        ) from exc


def filesystem_json(value: Any) -> str:
    try:
        validate_strict_json(value)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        ) + "\n"
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction content"
        ) from exc


def sha256_text(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def declaration_transaction_present(project: dict[str, Any] | None) -> bool:
    files = project.get("files") if isinstance(project, dict) else None
    return isinstance(files, dict) and TRANSACTION_PATH in files


def declaration_transaction_entry_present(root: str | Path) -> bool:
    path = Path(root).resolve().joinpath(*TRANSACTION_PATH.split("/"))
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def validate_declaration_transaction(journal: Any) -> dict[str, Any]:
    try:
        validate_strict_json(journal)
    except InvalidStrictJSONError as exc:
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction"
        ) from exc
    if not isinstance(journal, dict) or set(journal) != TRANSACTION_FIELDS:
        raise InvalidCompetencyDeclarationTransactionError("invalid declaration transaction")
    if journal["protocol"] != TRANSACTION_PROTOCOL:
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction protocol"
        )
    if journal["phase"] not in {"prepared", "committed"}:
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction phase"
        )
    digest = journal["declaration_digest"]
    transaction_id = journal["transaction_id"]
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction identity"
        )
    if not isinstance(transaction_id, str) or not transaction_id.startswith(
        "competency-declaration-transaction:"
    ):
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction identity"
        )
    expected_id = "competency-declaration-transaction:" + digest[7:]
    if transaction_id != expected_id:
        raise InvalidCompetencyDeclarationTransactionError(
            "incoherent declaration transaction identity"
        )
    paths = journal["paths"]
    created = journal["created_paths"]
    files = journal["files"]
    if not isinstance(paths, list) or not isinstance(created, list) or not isinstance(files, list):
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction plan"
        )
    if paths != sorted(set(paths)) or created != sorted(set(created)):
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction paths"
        )
    if not set(created).issubset(set(paths)):
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction created paths"
        )
    if not set(paths).issubset(ALLOWED_BUSINESS_PATHS):
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction path"
        )
    if len(paths) < 1:
        raise InvalidCompetencyDeclarationTransactionError(
            "declaration transaction requires at least one path"
        )
    file_paths = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != FILE_FIELDS:
            raise InvalidCompetencyDeclarationTransactionError(
                "invalid declaration transaction file"
            )
        if entry["path"] not in paths:
            raise InvalidCompetencyDeclarationTransactionError(
                "invalid declaration transaction file path"
            )
        if not all(
            isinstance(entry[field], str)
            for field in ("before_hash", "after_hash", "before_content", "after_content")
        ):
            raise InvalidCompetencyDeclarationTransactionError(
                "invalid declaration transaction content"
            )
        if not SHA256_PATTERN.fullmatch(entry["before_hash"]) or not SHA256_PATTERN.fullmatch(
            entry["after_hash"]
        ):
            raise InvalidCompetencyDeclarationTransactionError(
                "invalid declaration transaction hash"
            )
        if sha256_text(entry["before_content"]) != entry["before_hash"]:
            raise InvalidCompetencyDeclarationTransactionError(
                "invalid declaration transaction hash"
            )
        if sha256_text(entry["after_content"]) != entry["after_hash"]:
            raise InvalidCompetencyDeclarationTransactionError(
                "invalid declaration transaction hash"
            )
        file_paths.append(entry["path"])
    if file_paths != paths:
        raise InvalidCompetencyDeclarationTransactionError(
            "invalid declaration transaction files"
        )
    return journal


def build_declaration_transaction(
    current: dict[str, Any],
    desired: dict[str, Any],
    declaration_digest: str,
) -> dict[str, Any]:
    """Build a before/after journal for competencies + declarations ledger."""
    current_files = current.get("files", {})
    desired_files = desired.get("files", {})
    paths = sorted(
        path for path in ALLOWED_BUSINESS_PATHS
        if path in desired_files and desired_files.get(path) != current_files.get(path)
    )
    if not paths:
        raise InvalidCompetencyDeclarationTransactionError(
            "declaration transaction has no business changes"
        )
    created_paths = sorted(path for path in paths if path not in current_files)
    files = []
    for path in paths:
        after_content = filesystem_json(desired_files[path])
        if path in current_files:
            before_content = filesystem_json(current_files[path])
        else:
            before_content = ""
        files.append({
            "path": path,
            "before_hash": sha256_text(before_content),
            "after_hash": sha256_text(after_content),
            "before_content": before_content,
            "after_content": after_content,
        })
    journal = {
        "protocol": TRANSACTION_PROTOCOL,
        "transaction_id": "competency-declaration-transaction:" + declaration_digest[7:],
        "declaration_digest": declaration_digest,
        "phase": "prepared",
        "paths": paths,
        "created_paths": created_paths,
        "files": files,
    }
    return validate_declaration_transaction(journal)


def observe_file(root: Path, relative: str) -> str | None:
    path = root.joinpath(*relative.split("/"))
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InvalidCompetencyDeclarationTransactionError(
            "declaration transaction path cannot be observed"
        ) from exc
