"""Pure UPGRADE planner, ledger rules, and bounds. No workspace I/O."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from .strict_json import InvalidStrictJSONError, validate_strict_json
from .upgrade_compat import (
    LEDGER_PATH,
    MANIFEST_PATH,
    MAX_FILE_BYTES,
    MAX_JSON_DEPTH,
    MAX_MANAGED_PATHS,
    MAX_MIGRATIONS_PER_PLAN,
    MAX_TRANSACTION_BYTES,
    TARGET_WORKSPACE_SCHEMA_VERSION,
)


class MigrationFailure(Exception):
    """Declared business failure of one migration transform or postcondition."""

    def __init__(self, migration_id: str, message: str = "migration failed"):
        self.migration_id = migration_id
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    id: str
    from_version: str
    to_version: str
    paths: tuple[str, ...]
    transform: Callable[[dict[str, Any]], dict[str, Any]]
    precondition: Callable[[dict[str, Any]], bool] | None = None
    postcondition: Callable[[dict[str, Any]], bool] | None = None

    def fingerprint(self) -> str:
        payload = {
            "from_version": self.from_version,
            "id": self.id,
            "paths": list(self.paths),
            "to_version": self.to_version,
        }
        return _sha256_canonical(payload)


@dataclass(frozen=True, slots=True)
class UpgradePlan:
    status: str
    reason: str | None
    current_schema_version: str | None
    target_schema_version: str
    migrations: tuple[MigrationSpec, ...]

    @property
    def migration_ids(self) -> list[str]:
        return [item.id for item in self.migrations]


def _sha256_canonical(value: Any) -> str:
    validate_strict_json(value)
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def json_depth(value: Any) -> int:
    if isinstance(value, dict):
        if not value:
            return 1
        return 1 + max(json_depth(item) for item in value.values())
    if isinstance(value, list):
        if not value:
            return 1
        return 1 + max(json_depth(item) for item in value)
    return 0


def utf8_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    validate_strict_json(value)
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
    ) + "\n"
    return len(encoded.encode("utf-8"))


def version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("invalid schema version")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def validate_ledger(ledger: Any) -> dict[str, Any]:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("applied"), list):
        raise ValueError("invalid migration ledger")
    try:
        validate_strict_json(ledger)
    except InvalidStrictJSONError as exc:
        raise ValueError("invalid migration ledger") from exc
    seen: dict[str, dict[str, Any]] = {}
    for entry in ledger["applied"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid ledger entry")
        required = {"id", "from_version", "to_version", "status"}
        if not required.issubset(entry):
            raise ValueError("invalid ledger entry")
        migration_id = entry["id"]
        if not isinstance(migration_id, str) or not migration_id:
            raise ValueError("invalid ledger entry")
        previous = seen.get(migration_id)
        if previous is not None:
            if (
                previous.get("from_version") != entry.get("from_version")
                or previous.get("to_version") != entry.get("to_version")
                or previous.get("migration_fingerprint") != entry.get("migration_fingerprint")
            ):
                raise ValueError("ledger_id_conflict")
        seen[migration_id] = entry
    return ledger


def ledger_conflicts(ledger: dict[str, Any], spec: MigrationSpec) -> bool:
    for entry in ledger.get("applied", []):
        if entry.get("id") != spec.id:
            continue
        fingerprint = entry.get("migration_fingerprint")
        if (
            entry.get("from_version") != spec.from_version
            or entry.get("to_version") != spec.to_version
            or (fingerprint is not None and fingerprint != spec.fingerprint())
        ):
            return True
    return False


def already_applied(ledger: dict[str, Any], spec: MigrationSpec) -> bool:
    return any(
        entry.get("id") == spec.id and entry.get("status") == "applied"
        for entry in ledger.get("applied", [])
    )


def plan_upgrade(
    current_schema_version: str | None,
    *,
    target_schema_version: str = TARGET_WORKSPACE_SCHEMA_VERSION,
    migrations: tuple[MigrationSpec, ...] | list[MigrationSpec] = (),
    ledger: dict[str, Any] | None = None,
) -> UpgradePlan:
    target = target_schema_version
    empty = UpgradePlan("BLOCKED", None, current_schema_version, target, ())
    if current_schema_version is None:
        return UpgradePlan("BLOCKED", "workspace_not_initialized", None, target, ())
    try:
        current_tuple = version_tuple(current_schema_version)
        target_tuple = version_tuple(target)
    except ValueError:
        return UpgradePlan("BLOCKED", "invalid_schema_version", current_schema_version, target, ())
    if current_tuple > target_tuple:
        return UpgradePlan("BLOCKED", "future_schema", current_schema_version, target, ())
    if current_tuple == target_tuple:
        return UpgradePlan("NO_CHANGE", None, current_schema_version, target, ())

    stored = {"applied": []} if ledger is None else validate_ledger(deepcopy(ledger))
    ids = [item.id for item in migrations]
    if len(ids) != len(set(ids)):
        return empty.__class__("BLOCKED", "duplicate_migration_id", current_schema_version, target, ())

    path: list[MigrationSpec] = []
    cursor = current_schema_version
    seen: set[str] = set()
    registry = list(migrations)
    while cursor != target:
        if cursor in seen:
            return UpgradePlan("BLOCKED", "migration_cycle_detected", current_schema_version, target, ())
        seen.add(cursor)
        candidates = [item for item in registry if item.from_version == cursor]
        if len(candidates) > 1:
            return UpgradePlan("BLOCKED", "ambiguous_migration_path", current_schema_version, target, ())
        if not candidates:
            return UpgradePlan("BLOCKED", "migration_path_missing", current_schema_version, target, ())
        candidate = candidates[0]
        try:
            if version_tuple(candidate.to_version) > target_tuple:
                return UpgradePlan("BLOCKED", "migration_path_missing", current_schema_version, target, ())
            if version_tuple(candidate.to_version) <= version_tuple(cursor):
                return UpgradePlan("BLOCKED", "non_forward_migration", current_schema_version, target, ())
        except ValueError:
            return UpgradePlan("BLOCKED", "invalid_schema_version", current_schema_version, target, ())
        if ledger_conflicts(stored, candidate):
            return UpgradePlan("BLOCKED", "ledger_conflict", current_schema_version, target, ())
        path.append(candidate)
        cursor = candidate.to_version

    if len(path) > MAX_MIGRATIONS_PER_PLAN:
        return UpgradePlan("BLOCKED", "plan_exceeds_migration_bound", current_schema_version, target, ())
    managed = []
    for item in path:
        managed.extend(item.paths)
    if len(set(managed)) > MAX_MANAGED_PATHS:
        return UpgradePlan("BLOCKED", "plan_exceeds_path_bound", current_schema_version, target, ())
    if any(not path_name or path_name.count("/") < 1 for path_name in managed):
        return UpgradePlan("BLOCKED", "invalid_managed_path", current_schema_version, target, ())
    return UpgradePlan("CHANGE", None, current_schema_version, target, tuple(path))


def apply_plan_in_memory(
    project: dict[str, Any],
    plan: UpgradePlan,
) -> dict[str, Any]:
    """Apply transforms on deep copies. Updates schema_version after each step."""
    if plan.status != "CHANGE":
        return deepcopy(project)
    out = deepcopy(project)
    for spec in plan.migrations:
        if spec.precondition is not None and not spec.precondition(out):
            raise MigrationFailure(spec.id, "precondition failed")
        try:
            transformed = spec.transform(deepcopy(out))
        except MigrationFailure:
            raise
        except Exception as exc:
            raise MigrationFailure(spec.id, type(exc).__name__) from exc
        if spec.postcondition is not None and not spec.postcondition(transformed):
            raise MigrationFailure(spec.id, "postcondition failed")
        files = transformed.setdefault("files", {})
        manifest = files.get(MANIFEST_PATH)
        if isinstance(manifest, dict):
            manifest = deepcopy(manifest)
            manifest["schema_version"] = spec.to_version
            files[MANIFEST_PATH] = manifest
        out = transformed
    return out


def append_ledger_entries(
    ledger: dict[str, Any],
    plan: UpgradePlan,
    *,
    transaction_id: str,
    result_fingerprint: str,
) -> dict[str, Any]:
    out = deepcopy(validate_ledger(ledger))
    for spec in plan.migrations:
        if already_applied(out, spec):
            continue
        out["applied"].append({
            "id": spec.id,
            "from_version": spec.from_version,
            "to_version": spec.to_version,
            "status": "applied",
            "transaction_id": transaction_id,
            "migration_fingerprint": spec.fingerprint(),
            "result_fingerprint": result_fingerprint,
        })
    return out


def enforce_content_bounds(files: dict[str, Any], paths: list[str]) -> str | None:
    total = 0
    for path in paths:
        if path not in files:
            continue
        value = files[path]
        try:
            depth = json_depth(value) if not isinstance(value, str) else 0
            size = utf8_size(value)
        except (InvalidStrictJSONError, TypeError, ValueError):
            return "invalid_projected_json"
        if depth > MAX_JSON_DEPTH:
            return "json_depth_exceeded"
        if size > MAX_FILE_BYTES:
            return "file_size_exceeded"
        total += size
    if total > MAX_TRANSACTION_BYTES:
        return "transaction_size_exceeded"
    return None


def result_fingerprint(files: dict[str, Any], paths: list[str]) -> str:
    payload = {path: files.get(path) for path in sorted(paths)}
    return _sha256_canonical(payload)


def changed_paths(current: dict[str, Any], desired: dict[str, Any]) -> list[str]:
    before = current.get("files", {})
    after = desired.get("files", {})
    return sorted(
        path for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def default_ledger(project: dict[str, Any]) -> dict[str, Any]:
    ledger = project.get("files", {}).get(LEDGER_PATH, {"applied": []})
    if ledger == {"applied": []} or isinstance(ledger, dict):
        return validate_ledger(deepcopy(ledger) if isinstance(ledger, dict) else {"applied": []})
    raise ValueError("invalid migration ledger")
