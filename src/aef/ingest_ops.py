from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .filesystem import (
    _evaluation_transaction_entry_present,
    apply_workspace,
    is_link_or_reparse_point,
    load_workspace,
    plan_workspace,
    workspace_mutation_lock,
)
from .ingest_intake import (
    IngestBlockedError,
    InvalidIngestSubmissionError,
    attach_source_records,
    bind_ingest_citations,
    event_citations,
    flatten_ingest_events,
    merge_existing_source_records,
    validate_ingest_submission,
)
from .knowledge import EvidenceCapExceededError
from .learning_engine import ingest_events
from .record_document import (
    InvalidPersistedRecordError,
    InvalidRecordSubmissionError,
    record_relative_path,
    validate_persisted_record,
)
from .schema_validation import validate_persisted_knowledge
from .competency_declaration_transaction import (
    declaration_transaction_entry_present,
    declaration_transaction_present,
)
from .transaction_guard import evaluation_recovery_required
from .upgrade_transaction import (
    upgrade_transaction_entry_present,
    upgrade_transaction_present,
)


KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"
MANIFEST_PATH = ".agent/manifest.json"
RECORDS_DIRECTORY = ".agent/records"


def _blocked(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise IngestBlockedError(code, message, details)


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
        raise IngestBlockedError(
            "upgrade_recovery_required",
            "upgrade recovery is required before workspace mutation",
        )
    if evaluation_recovery_required(current) or _evaluation_transaction_entry_present(root):
        raise IngestBlockedError(
            "evaluation_recovery_required",
            "evaluation recovery is required before workspace mutation",
        )
    if declaration_transaction_present(current) or declaration_transaction_entry_present(root):
        raise IngestBlockedError(
            "competency_declaration_recovery_required",
            "competency declaration recovery is required before workspace mutation",
        )


def _reject_exterior_link(path: Path, *, code: str, message: str) -> None:
    if path.exists() and is_link_or_reparse_point(path):
        _blocked(code, message)


def _load_persisted_records(
    root: Path,
    current: dict[str, Any],
    record_ids: list[str],
) -> dict[str, Any]:
    records_dir = root.joinpath(*RECORDS_DIRECTORY.split("/"))
    _reject_exterior_link(
        records_dir,
        code="record_target_unsafe",
        message="the records directory is not a regular directory.",
    )
    knowledge_path = root.joinpath(*KNOWLEDGE_PATH.split("/"))
    _reject_exterior_link(
        knowledge_path,
        code="knowledge_target_unsafe",
        message="the knowledge path is not a regular file.",
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


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _ids(items: list[Any]) -> list[str]:
    return [
        item["id"] for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _projected(state: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "signals": _ids(state.get("signals") or []),
        "observations": _ids(state.get("observations") or []),
        "hypotheses": _ids(state.get("hypotheses") or []),
    }


def plan_ingest(
    root: str | Path,
    document: Any,
    *,
    dry_run: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    """Validate intake and project knowledge. Write only when dry_run is false."""
    intake = validate_ingest_submission(document)
    root = Path(root).resolve()
    preview = load_workspace(root)
    preview_files = preview.get("files") if isinstance(preview, dict) else None
    if (
        not isinstance(preview_files, dict)
        or MANIFEST_PATH not in preview_files
        or KNOWLEDGE_PATH not in preview_files
    ):
        _require_initialized(preview)

    with workspace_mutation_lock(root):
        current = load_workspace(root)
        _guard_transactions(root, current)
        knowledge = deepcopy(_require_initialized(current))
        record_ids = [citation["record_id"] for citation in intake["records"]]
        persisted = _load_persisted_records(root, current, record_ids)
        bind_ingest_citations(intake, persisted)
        events = flatten_ingest_events(intake)
        citations = event_citations(intake)
        try:
            _, next_state = ingest_events(knowledge, events)
            next_state = attach_source_records(next_state, citations)
            next_state = merge_existing_source_records(knowledge, next_state)
        except EvidenceCapExceededError as exc:
            _blocked(
                exc.code,
                "the evidence id union would exceed the configured cap.",
            )
        try:
            validate_persisted_knowledge(next_state)
        except Exception as exc:
            raise InvalidIngestSubmissionError(
                "invalid_knowledge_state",
                "projected knowledge is not a valid persisted knowledge document.",
            ) from exc

        desired = deepcopy(current)
        desired.setdefault("files", {})
        desired["files"][KNOWLEDGE_PATH] = deepcopy(next_state)
        status = "NO_CHANGE" if next_state == knowledge else "CHANGE"
        result = {
            "records": record_ids,
            "events_accepted": len(events),
            "projected": _projected(next_state),
            "knowledge_path": KNOWLEDGE_PATH,
            "human_action_required": False,
        }
        meta: dict[str, Any] = {}
        if status == "NO_CHANGE" or dry_run:
            diff = (
                {"created": [], "modified": [], "removed": []}
                if status == "NO_CHANGE"
                else plan_workspace(current, desired)
            )
            return status, result, meta, diff
        return status, result, meta, apply_workspace(root, current, desired)


INGEST_DERIVED_PREFIXES = (
    "signal:novelty:",
    "signal:repeated-help:",
    "signal:convergent-corrections:",
    "signal:rule-surprise:",
    "signal:unexplained-success:",
    "observation:signal:",
    "hypothesis:",
)

INGEST_DERIVED_ANNOUNCEMENT = (
    "Derives learning signals, observations, and candidate hypotheses only."
)


def _is_ingest_derived(item_id: Any) -> bool:
    return isinstance(item_id, str) and item_id.startswith(INGEST_DERIVED_PREFIXES)


def _source_records_of(item: dict[str, Any]) -> list[dict[str, str]] | None:
    raw = item.get("source_records")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        return []
    sources = []
    for entry in raw:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("record_id"), str)
            and isinstance(entry.get("digest"), str)
        ):
            sources.append({"record_id": entry["record_id"], "digest": entry["digest"]})
    return sources


def _error_finding(finding_id: str) -> dict[str, str]:
    return {"id": finding_id, "severity": "error"}


def audit_ingest_provenance(project: dict[str, Any], root: str | Path | None = None) -> list[dict[str, str]]:
    """Read-only provenance findings. Absence of ingest is not a finding."""
    files = project.get("files") if isinstance(project, dict) else None
    if not isinstance(files, dict):
        return []
    knowledge = files.get(KNOWLEDGE_PATH)
    if not isinstance(knowledge, dict):
        return []
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    root_path = Path(root).resolve() if root is not None else None

    def add(finding_id: str) -> None:
        if finding_id not in seen:
            seen.add(finding_id)
            findings.append(_error_finding(finding_id))

    for collection in ("signals", "observations", "hypotheses"):
        for item in knowledge.get(collection) or []:
            if not isinstance(item, dict) or not _is_ingest_derived(item.get("id")):
                continue
            sources = _source_records_of(item)
            if not sources:
                add("knowledge-missing-provenance")
                continue
            for source in sources:
                _audit_cited_record(source, files, root_path, add)
    return findings


def _audit_cited_record(
    source: dict[str, str],
    files: dict[str, Any],
    root: Path | None,
    add,
) -> None:
    try:
        relative = record_relative_path(source["record_id"])
    except InvalidRecordSubmissionError:
        add("ingest-record-missing")
        return
    stored = files.get(relative)
    if stored is None and root is not None:
        target = root.joinpath(*relative.split("/"))
        if not target.is_file():
            add("ingest-record-missing")
            return
        try:
            stored = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return
    if stored is None:
        add("ingest-record-missing")
        return
    if not isinstance(stored, dict):
        return
    try:
        persisted = validate_persisted_record(stored)
    except (InvalidPersistedRecordError, InvalidRecordSubmissionError):
        return
    if persisted.get("digest") != source["digest"]:
        add("ingest-record-digest-mismatch")


def apply_ingest(
    root: str | Path, document: Any
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    return plan_ingest(root, document, dry_run=False)
