"""Plan / apply / recover / audit competency declarations."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .competency_declaration import (
    COMPETENCIES_PATH,
    LEDGER_PATH,
    CompetencyDeclarationBlockedError,
    InvalidCompetencyDeclarationError,
    bind_declaration_records,
    declaration_digest,
    empty_ledger,
    projected_l1_entry,
    resolve_declaration_outcome,
    validate_competency_declaration,
    validate_ledger,
)
from .competency_declaration_transaction import (
    TRANSACTION_PATH,
    InvalidCompetencyDeclarationTransactionError,
    build_declaration_transaction,
    declaration_transaction_entry_present,
    declaration_transaction_present,
    observe_file,
    sha256_text,
    validate_declaration_transaction,
)
from .competency_learning import ensure_competency
from .filesystem import (
    _apply_workspace_unchecked,
    is_link_or_reparse_point,
    load_workspace,
    plan_workspace,
)
from .record_document import (
    InvalidPersistedRecordError,
    InvalidRecordSubmissionError,
    record_relative_path,
    validate_persisted_record,
)
from .transaction_guard import evaluation_recovery_required
from .upgrade_transaction import (
    upgrade_transaction_entry_present,
    upgrade_transaction_present,
)


MANIFEST_PATH = ".agent/manifest.json"
RECORDS_DIRECTORY = ".agent/records"


def _blocked(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise CompetencyDeclarationBlockedError(code, message, details)


def _require_initialized(current: dict[str, Any]) -> dict[str, Any]:
    files = current.get("files") if isinstance(current, dict) else None
    if not isinstance(files, dict):
        _blocked("workspace_not_initialized", "the workspace is not an initialized AEF project.")
    if MANIFEST_PATH not in files or COMPETENCIES_PATH not in files:
        _blocked("workspace_not_initialized", "the workspace is not an initialized AEF project.")
    competencies = files[COMPETENCIES_PATH]
    if not isinstance(competencies, dict):
        _blocked("invalid_competency_state", "persisted competencies are not a JSON object.")
    return competencies


def _guard_transactions(root: Path, current: dict[str, Any]) -> None:
    if upgrade_transaction_present(current) or upgrade_transaction_entry_present(root):
        _blocked(
            "upgrade_recovery_required",
            "upgrade recovery is required before workspace mutation",
        )
    if evaluation_recovery_required(current):
        _blocked(
            "evaluation_recovery_required",
            "evaluation recovery is required before workspace mutation",
        )
    if declaration_transaction_present(current) or declaration_transaction_entry_present(root):
        _blocked(
            "competency_declaration_recovery_required",
            "competency declaration recovery is required before workspace mutation",
        )


def _reject_exterior_link(path: Path, *, code: str, message: str) -> None:
    if path.exists() and is_link_or_reparse_point(path):
        _blocked(code, message)


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
    _reject_exterior_link(
        records_dir,
        code="record_target_unsafe",
        message="the records directory is not a regular directory.",
    )
    competencies_path = root.joinpath(*COMPETENCIES_PATH.split("/"))
    _reject_exterior_link(
        competencies_path,
        code="competency_target_unsafe",
        message="the competencies path is not a regular file.",
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


def _load_ledger(files: dict[str, Any]) -> dict[str, Any]:
    raw = files.get(LEDGER_PATH)
    if raw is None:
        return empty_ledger()
    try:
        return validate_ledger(raw)
    except InvalidCompetencyDeclarationError as exc:
        raise CompetencyDeclarationBlockedError(exc.code, str(exc)) from exc


def _result_payload(
    document: dict[str, Any],
    *,
    human_action_required: bool = False,
) -> dict[str, Any]:
    entry = projected_l1_entry(document)
    return {
        "competency_id": document["competency_id"],
        "projected": entry,
        "records": [
            {"record_id": item["record_id"], "digest": item["digest"]}
            for item in document["records"]
        ],
        "human_action_required": human_action_required,
        "competencies_path": COMPETENCIES_PATH,
        "ledger_path": LEDGER_PATH,
    }


def plan_declare(
    root: str | Path,
    document: Any,
    *,
    dry_run: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, list[str]] | None]:
    """Validate declaration and project L1 birth. Write only when dry_run is false."""
    declaration = validate_competency_declaration(document)
    root = Path(root).resolve()
    current = load_workspace(root)
    _guard_transactions(root, current)
    competencies = deepcopy(_require_initialized(current))
    ledger = _load_ledger(current.get("files", {}))
    record_ids = [citation["record_id"] for citation in declaration["records"]]
    persisted = _load_persisted_records(root, current, record_ids)
    bind_declaration_records(declaration, persisted)

    # Internal L1 primitive — public contract remains decision + records + ledger.
    status, next_competencies, next_ledger = resolve_declaration_outcome(
        declaration, competencies, ledger,
    )
    if status == "CHANGE":
        agent = {"competencies": deepcopy(competencies)}
        _, agent = ensure_competency(
            agent,
            declaration["competency_id"],
            title=declaration["title"],
            source="declared",
        )
        next_competencies[declaration["competency_id"]] = projected_l1_entry(declaration)
        # ensure_competency shape must match projected birth entry.
        if agent["competencies"][declaration["competency_id"]] != next_competencies[
            declaration["competency_id"]
        ]:
            raise InvalidCompetencyDeclarationError(
                "invalid_initial_state",
                "ensure_competency projection diverged from declaration L1 contract.",
            )

    desired = deepcopy(current)
    desired.setdefault("files", {})
    desired["files"][COMPETENCIES_PATH] = deepcopy(next_competencies)
    desired["files"][LEDGER_PATH] = deepcopy(next_ledger)
    result = _result_payload(declaration)
    meta: dict[str, Any] = {}
    if status == "NO_CHANGE" or dry_run:
        diff = (
            {"created": [], "modified": [], "removed": []}
            if status == "NO_CHANGE"
            else plan_workspace(current, desired)
        )
        return status, result, meta, diff

    digest = declaration_digest(declaration)
    journal = build_declaration_transaction(current, desired, digest)
    _apply_declaration_transaction(root, current, desired, journal)
    return status, result, meta, plan_workspace(current, desired)


def _apply_declaration_transaction(
    root: Path,
    current: dict[str, Any],
    desired: dict[str, Any],
    journal: dict[str, Any],
) -> None:
    prepared = deepcopy(current)
    prepared.setdefault("files", {})[TRANSACTION_PATH] = journal
    _apply_workspace_unchecked(root, current, prepared, allow_delete=False)

    mutating = deepcopy(prepared)
    for path in journal["paths"]:
        mutating["files"][path] = deepcopy(desired["files"][path])
    _apply_workspace_unchecked(root, prepared, mutating, allow_delete=False)

    for entry in journal["files"]:
        observed = observe_file(root, entry["path"])
        if observed is None or sha256_text(observed) != entry["after_hash"]:
            raise CompetencyDeclarationBlockedError(
                "declaration_hash_mismatch",
                "declaration transaction hashes do not match written content.",
            )

    committed = deepcopy(journal)
    committed["phase"] = "committed"
    committed_project = deepcopy(mutating)
    committed_project["files"][TRANSACTION_PATH] = committed
    _apply_workspace_unchecked(root, mutating, committed_project, allow_delete=False)

    final = load_workspace(root)
    cleanup = deepcopy(final)
    cleanup.get("files", {}).pop(TRANSACTION_PATH, None)
    _apply_workspace_unchecked(root, final, cleanup, allow_delete=True)


def recover_declaration(
    root: str | Path,
    *,
    dry_run: bool = True,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, list[str]] | None]:
    """Recover an interrupted competency declaration transaction."""
    root = Path(root).resolve()
    project = load_workspace(root)
    files = project.get("files", {})
    raw = files.get(TRANSACTION_PATH)
    if raw is None and not declaration_transaction_entry_present(root):
        return (
            "NO_CHANGE",
            {"recovery_action": "none"},
            {"reason": "no_competency_declaration_transaction"},
            {"created": [], "modified": [], "removed": []},
        )
    try:
        journal = validate_declaration_transaction(raw)
    except (InvalidCompetencyDeclarationTransactionError, TypeError, ValueError):
        raise CompetencyDeclarationBlockedError(
            "invalid_competency_declaration_transaction",
            "the competency declaration transaction is invalid.",
        )

    observations = {}
    for entry in journal["files"]:
        observations[entry["path"]] = observe_file(root, entry["path"])

    before_ok = all(
        (observations[entry["path"]] is None and entry["path"] in journal["created_paths"])
        or (
            observations[entry["path"]] is not None
            and sha256_text(observations[entry["path"]]) == entry["before_hash"]
        )
        for entry in journal["files"]
    )
    after_ok = all(
        observations[entry["path"]] is not None
        and sha256_text(observations[entry["path"]]) == entry["after_hash"]
        for entry in journal["files"]
    )
    if journal["phase"] == "prepared" and (before_ok or after_ok):
        action = "rollback"
    elif journal["phase"] == "committed" and after_ok:
        action = "finalize"
    else:
        raise CompetencyDeclarationBlockedError(
            "competency_declaration_transaction_inconsistent",
            "the competency declaration transaction cannot be recovered automatically.",
        )

    result = {
        "recovery_action": action,
        "transaction_id": journal["transaction_id"],
        "human_action_required": False,
    }
    if dry_run:
        return "CHANGE", result, {}, {"created": [], "modified": [], "removed": []}

    if action == "rollback":
        restored = deepcopy(project)
        for entry in journal["files"]:
            if entry["path"] in journal["created_paths"]:
                restored.get("files", {}).pop(entry["path"], None)
            else:
                try:
                    parsed = json.loads(entry["before_content"])
                    restored.setdefault("files", {})[entry["path"]] = parsed
                except Exception:
                    restored.setdefault("files", {})[entry["path"]] = entry["before_content"]
        restored.get("files", {}).pop(TRANSACTION_PATH, None)
        _apply_workspace_unchecked(root, project, restored, allow_delete=True)
    else:
        cleanup = deepcopy(project)
        cleanup.get("files", {}).pop(TRANSACTION_PATH, None)
        _apply_workspace_unchecked(root, project, cleanup, allow_delete=True)
    return (
        "CHANGE",
        result,
        {},
        {"created": [], "modified": [], "removed": [TRANSACTION_PATH]},
    )


def audit_declaration_provenance(
    project: dict[str, Any],
    root: str | Path | None = None,
) -> list[dict[str, str]]:
    """Read-only provenance findings for competency births."""
    files = project.get("files") if isinstance(project, dict) else None
    if not isinstance(files, dict):
        return []
    findings: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(finding_id: str, severity: str) -> None:
        key = finding_id
        if key not in seen:
            seen.add(key)
            findings.append({"id": finding_id, "severity": severity})

    if declaration_transaction_present(project) or (
        root is not None and declaration_transaction_entry_present(root)
    ):
        add("competency-declaration-recovery-required", "error")
        raw = files.get(TRANSACTION_PATH)
        if raw is not None:
            try:
                validate_declaration_transaction(raw)
            except (InvalidCompetencyDeclarationTransactionError, TypeError, ValueError):
                add("invalid-competency-declaration-transaction", "error")

    competencies = files.get(COMPETENCIES_PATH)
    if not isinstance(competencies, dict):
        return findings
    if competencies == {}:
        return findings

    ledger_raw = files.get(LEDGER_PATH)
    events_by_id: dict[str, dict[str, Any]] = {}
    if ledger_raw is not None:
        try:
            ledger = validate_ledger(ledger_raw)
        except InvalidCompetencyDeclarationError:
            add("invalid-competency-declaration-ledger", "error")
            ledger = None
        if isinstance(ledger, dict):
            for event in ledger.get("events") or []:
                if isinstance(event, dict) and isinstance(event.get("competency_id"), str):
                    events_by_id[event["competency_id"]] = event

    # Legacy list envelope: treat listed ids if present.
    entries: dict[str, Any]
    if "competencies" in competencies and isinstance(competencies.get("competencies"), list):
        entries = {}
        for item in competencies["competencies"]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                entries[item["id"]] = item
    else:
        entries = {
            key: value for key, value in competencies.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    for competency_id, entry in entries.items():
        event = events_by_id.get(competency_id)
        if event is None:
            add("competency-missing-declaration-provenance", "warning")
            continue
        for citation in event.get("records") or []:
            if not isinstance(citation, dict):
                add("competency-declaration-record-invalid", "error")
                continue
            record_id = citation.get("record_id")
            digest = citation.get("digest")
            if not isinstance(record_id, str) or not isinstance(digest, str):
                add("competency-declaration-record-invalid", "error")
                continue
            try:
                relative = record_relative_path(record_id)
            except InvalidRecordSubmissionError:
                add("competency-declaration-record-missing", "error")
                continue
            stored = files.get(relative)
            if stored is None:
                add("competency-declaration-record-missing", "error")
                continue
            if not isinstance(stored, dict) or stored.get("digest") != digest:
                add("competency-declaration-record-digest-mismatch", "error")
        if not isinstance(entry, dict):
            continue
        if entry.get("level") != "L1" and event.get("declaration_digest"):
            # Birth event present but state drifted — still not inventing provenance.
            pass
    return findings
