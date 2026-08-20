from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .filesystem import RECORDS_DIRECTORY, is_link_or_reparse_point
from .record_document import InvalidPersistedRecordError, validate_persisted_record


def _error(finding_id: str) -> dict[str, str]:
    return {"id": finding_id, "severity": "error"}


def _entry_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _has_case_collision(names: list[str]) -> bool:
    folded: dict[str, set[str]] = {}
    for name in names:
        folded.setdefault(name.casefold(), set()).add(name)
    return any(len(group) > 1 for group in folded.values())


def audit_records_directory(root: str | Path) -> list[dict[str, str]]:
    """Inspect .agent/records on disk. Absence is not an error."""
    records = Path(root).joinpath(*RECORDS_DIRECTORY.split("/"))
    if not _entry_exists(records):
        return []
    if is_link_or_reparse_point(records) or not records.is_dir():
        return [_error("record-symlink")]

    try:
        entries = list(os.scandir(records))
    except OSError:
        return [_error("record-unexpected-entry")]

    findings: list[dict[str, str]] = []
    if _has_case_collision([entry.name for entry in entries]):
        findings.append(_error("record-case-collision"))

    for entry in entries:
        path = Path(entry.path)
        if entry.is_symlink() or is_link_or_reparse_point(path):
            findings.append(_error("record-symlink"))
            continue
        if not entry.is_file(follow_symlinks=False):
            findings.append(_error("record-unexpected-entry"))
            continue
        if len(entry.name) < 6 or entry.name[-5:] != ".json":
            findings.append(_error("record-unexpected-entry"))
            continue
        stem = entry.name[:-5]
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings.append(_error("invalid-record"))
            continue
        if not isinstance(document, dict):
            findings.append(_error("invalid-record"))
            continue
        if document.get("record_id") != stem:
            findings.append(_error("record-id-path-mismatch"))
        try:
            validate_persisted_record(document)
        except InvalidPersistedRecordError as exc:
            findings.append(_error(exc.code))
    return findings


def audit_records_in_files(files: dict[str, Any]) -> list[dict[str, str]]:
    """Validate in-memory record documents when no workspace root is supplied."""
    findings: list[dict[str, str]] = []
    prefix = RECORDS_DIRECTORY + "/"
    for rel, document in files.items():
        if not rel.startswith(prefix) or not rel.endswith(".json"):
            continue
        stem = rel[len(prefix):-5]
        if "/" in stem:
            findings.append(_error("record-unexpected-entry"))
            continue
        if not isinstance(document, dict):
            findings.append(_error("invalid-record"))
            continue
        if document.get("record_id") != stem:
            findings.append(_error("record-id-path-mismatch"))
        try:
            validate_persisted_record(document)
        except InvalidPersistedRecordError as exc:
            findings.append(_error(exc.code))
    return findings
