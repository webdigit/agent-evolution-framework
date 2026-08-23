from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .filesystem import (
    EVALUATION_TRANSACTION_PATH,
    _apply_workspace_transaction,
    _transaction_write_capability,
    apply_workspace,
    load_workspace,
    plan_workspace,
)
from .strict_json import InvalidStrictJSONError, reject_duplicate_keys, validate_strict_json


TRANSACTION_PATH = EVALUATION_TRANSACTION_PATH
TRANSACTION_PROTOCOL = "aef.evaluate-transaction/v1"
TRANSACTION_FIELDS = {
    "protocol", "transaction_id", "decision_batch_digest", "phase", "paths", "files",
}
TRANSACTION_FILE_FIELDS = {
    "path", "before_hash", "after_hash", "before_content", "after_content",
}
TRANSACTION_STATE_PATHS = {
    ".agent/state/career.json",
    ".agent/state/competencies.json",
    ".agent/state/evaluations.json",
}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class InvalidEvaluationTransactionError(ValueError):
    """Raised when the recoverable EVALUATE journal is invalid."""


def _canonical_json(value):
    try:
        validate_strict_json(value)
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidEvaluationTransactionError("invalid transaction JSON") from exc


def _filesystem_json(value):
    try:
        validate_strict_json(value)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        ) + "\n"
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidEvaluationTransactionError("invalid transaction content") from exc


def _sha256_bytes(content):
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def decision_batch_digest(document):
    return _sha256_bytes(_canonical_json(document))


def _parse_strict_content(content):
    def reject_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        parsed = json.loads(
            content,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
        validate_strict_json(parsed)
    except (json.JSONDecodeError, InvalidStrictJSONError, ValueError) as exc:
        raise InvalidEvaluationTransactionError(
            "invalid evaluation transaction JSON content"
        ) from exc
    if not isinstance(parsed, dict) or _filesystem_json(parsed) != content:
        raise InvalidEvaluationTransactionError(
            "non-canonical evaluation transaction content"
        )
    return parsed


def validate_evaluation_transaction(journal):
    try:
        validate_strict_json(journal)
    except InvalidStrictJSONError as exc:
        raise InvalidEvaluationTransactionError("invalid evaluation transaction") from exc
    if not isinstance(journal, dict) or set(journal) != TRANSACTION_FIELDS:
        raise InvalidEvaluationTransactionError("invalid evaluation transaction")
    if journal["protocol"] != TRANSACTION_PROTOCOL:
        raise InvalidEvaluationTransactionError("invalid evaluation transaction protocol")
    transaction_id = journal["transaction_id"]
    digest = journal["decision_batch_digest"]
    if (
        not isinstance(transaction_id, str)
        or not isinstance(digest, str)
        or not SHA256_PATTERN.fullmatch(digest)
    ):
        raise InvalidEvaluationTransactionError("invalid evaluation transaction identity")
    if journal["phase"] not in {"prepared", "committed"}:
        raise InvalidEvaluationTransactionError("invalid evaluation transaction phase")
    paths = journal["paths"]
    files = journal["files"]
    if not isinstance(paths, list) or not isinstance(files, list):
        raise InvalidEvaluationTransactionError("invalid evaluation transaction plan")
    if not all(isinstance(path, str) for path in paths):
        raise InvalidEvaluationTransactionError("invalid evaluation transaction path")
    if not all(isinstance(entry, dict) for entry in files):
        raise InvalidEvaluationTransactionError("invalid evaluation transaction file")
    if len(paths) < 2 or len(files) < 2:
        raise InvalidEvaluationTransactionError("evaluation transaction must be multi-file")
    if ".agent/state/evaluations.json" not in paths:
        raise InvalidEvaluationTransactionError("evaluation transaction misses evaluations")
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        raise InvalidEvaluationTransactionError("invalid evaluation transaction paths")
    file_paths = []
    for entry in files:
        if set(entry) != TRANSACTION_FILE_FIELDS:
            raise InvalidEvaluationTransactionError("invalid evaluation transaction file")
        path = entry["path"]
        before_hash = entry["before_hash"]
        after_hash = entry["after_hash"]
        before_content = entry["before_content"]
        after_content = entry["after_content"]
        if not isinstance(path, str):
            raise InvalidEvaluationTransactionError("invalid evaluation transaction path")
        if not all(
            isinstance(value, str)
            for value in (before_hash, after_hash, before_content, after_content)
        ):
            raise InvalidEvaluationTransactionError("invalid evaluation transaction content")
        if not SHA256_PATTERN.fullmatch(before_hash) or not SHA256_PATTERN.fullmatch(
            after_hash
        ):
            raise InvalidEvaluationTransactionError("invalid evaluation transaction hash")
        file_paths.append(path)
        if path not in TRANSACTION_STATE_PATHS:
            raise InvalidEvaluationTransactionError("invalid evaluation transaction path")
        for content, expected_hash in (
            (before_content, before_hash), (after_content, after_hash)
        ):
            if not isinstance(content, str) or not isinstance(expected_hash, str):
                raise InvalidEvaluationTransactionError("invalid evaluation transaction content")
            if _sha256_bytes(content) != expected_hash:
                raise InvalidEvaluationTransactionError("incoherent evaluation transaction hash")
            _parse_strict_content(content)
    if file_paths != paths:
        raise InvalidEvaluationTransactionError("incoherent evaluation transaction paths")
    expected_id = "evaluation-transaction:" + digest[7:]
    if transaction_id != expected_id:
        raise InvalidEvaluationTransactionError("incoherent evaluation transaction identity")
    return journal


def build_evaluation_transaction(current, desired, decision_document):
    """Build a deterministic before/after journal for a multi-file plan."""
    diff = plan_workspace(current, desired)
    paths = sorted(diff["created"] + diff["modified"])
    if len(paths) < 2:
        return None
    digest = decision_batch_digest(decision_document)
    files = []
    for path in paths:
        if path not in current.get("files", {}) or path not in desired.get("files", {}):
            raise InvalidEvaluationTransactionError(
                "evaluation transactions require existing JSON state files"
            )
        before_content = _filesystem_json(current["files"][path])
        after_content = _filesystem_json(desired["files"][path])
        files.append({
            "path": path,
            "before_hash": _sha256_bytes(before_content),
            "after_hash": _sha256_bytes(after_content),
            "before_content": before_content,
            "after_content": after_content,
        })
    journal = {
        "protocol": TRANSACTION_PROTOCOL,
        "transaction_id": "evaluation-transaction:" + digest[7:],
        "decision_batch_digest": digest,
        "phase": "prepared",
        "paths": paths,
        "files": files,
    }
    validate_evaluation_transaction(journal)
    return journal


def _with_journal(project, journal):
    out = deepcopy(project)
    out.setdefault("files", {})[TRANSACTION_PATH] = deepcopy(journal)
    return out


def _without_journal(project):
    out = deepcopy(project)
    out.setdefault("files", {}).pop(TRANSACTION_PATH, None)
    return out


def _transaction_apply(
    root, current, desired, journal, phase, *, target_path=None, allow_delete=False
):
    capability = _transaction_write_capability(
        root, journal, phase, target_path=target_path
    )
    return _apply_workspace_transaction(
        root, current, desired, capability, allow_delete=allow_delete
    )


def apply_evaluation_transaction(
    root, current, desired, decision_document, *, dry_run=False, fault=None
):
    """Persist EVALUATE state, journaling only multi-file changes."""
    journal = build_evaluation_transaction(current, desired, decision_document)
    if journal is None:
        diff = plan_workspace(current, desired)
        if not dry_run:
            diff = apply_workspace(root, current, desired)
        return diff, None
    business_diff = plan_workspace(current, desired)
    if dry_run:
        return business_diff, deepcopy(journal)

    prepared = _with_journal(current, journal)
    _transaction_apply(root, current, prepared, journal, "prepare")
    if fault:
        fault("prepared", 0)
    working = load_workspace(root)
    for index, entry in enumerate(journal["files"], start=1):
        step = deepcopy(working)
        step["files"][entry["path"]] = deepcopy(desired["files"][entry["path"]])
        _transaction_apply(
            root, working, step, journal, "apply", target_path=entry["path"]
        )
        working = load_workspace(root)
        if fault:
            fault("file", index)

    committed_journal = deepcopy(journal)
    committed_journal["phase"] = "committed"
    committed = _with_journal(working, committed_journal)
    _transaction_apply(root, working, committed, journal, "commit")
    if fault:
        fault("committed", len(journal["files"]))
    working = load_workspace(root)
    _transaction_apply(
        root, working, _without_journal(working), committed_journal,
        "cleanup", allow_delete=True,
    )
    return business_diff, committed_journal


def _actual_hash(root, path):
    target = Path(root).resolve().joinpath(*path.split("/"))
    if not target.is_file():
        return None
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def recover_evaluation_transaction(root, current, *, dry_run=False):
    """Rollback prepared work or finalize an already committed transaction."""
    journal = current.get("files", {}).get(TRANSACTION_PATH)
    if journal is None:
        return "NO_CHANGE", deepcopy(current), {
            "reason": None, "transaction_id": None, "action": "none",
        }
    from .competency_declaration_transaction import (
        declaration_transaction_entry_present,
        declaration_transaction_present,
    )
    from .upgrade_transaction import (
        upgrade_transaction_entry_present,
        upgrade_transaction_present,
    )
    if upgrade_transaction_present(current) or upgrade_transaction_entry_present(root):
        return "BLOCKED", deepcopy(current), {
            "reason": "upgrade_recovery_required",
            "transaction_id": None, "action": "none",
        }
    if declaration_transaction_present(current) or declaration_transaction_entry_present(root):
        return "BLOCKED", deepcopy(current), {
            "reason": "competency_declaration_recovery_required",
            "transaction_id": None, "action": "none",
        }
    validate_evaluation_transaction(journal)
    states = []
    for entry in journal["files"]:
        actual = _actual_hash(root, entry["path"])
        if actual == entry["before_hash"]:
            states.append("before")
        elif actual == entry["after_hash"]:
            states.append("after")
        else:
            return "BLOCKED", deepcopy(current), {
                "reason": "evaluation_recovery_conflict",
                "transaction_id": journal["transaction_id"], "action": "none",
                "path": entry["path"],
            }
    if journal["phase"] == "committed" and any(state != "after" for state in states):
        return "BLOCKED", deepcopy(current), {
            "reason": "evaluation_recovery_conflict",
            "transaction_id": journal["transaction_id"], "action": "none",
        }
    action = "finalize" if journal["phase"] == "committed" else "rollback"
    desired = deepcopy(current)
    if action == "rollback":
        for entry in journal["files"]:
            desired["files"][entry["path"]] = json.loads(entry["before_content"])
    desired = _without_journal(desired)
    if not dry_run:
        _transaction_apply(
            root, current, desired, journal,
            "rollback" if action == "rollback" else "cleanup", allow_delete=True,
        )
    return "CHANGE", desired, {
        "reason": None, "transaction_id": journal["transaction_id"],
        "action": action,
    }
