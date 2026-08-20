"""Workspace I/O for UPGRADE. Does not wrap lab upgrade_project/release."""

from __future__ import annotations

import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

from .filesystem import (
    EVALUATION_TRANSACTION_PATH,
    WorkspacePathError,
    _apply_workspace_unchecked,
    is_link_or_reparse_point,
    load_workspace,
)
from .transaction_guard import evaluation_recovery_required
from .upgrade_compat import (
    LEDGER_PATH,
    MANIFEST_PATH,
    TARGET_WORKSPACE_SCHEMA_VERSION,
    UPGRADE_TRANSACTION_PATH,
    installed_package_version,
    ordered_migrations,
)
from .upgrade_plan import (
    MigrationFailure,
    MigrationSpec,
    UpgradePlan,
    append_ledger_entries,
    apply_plan_in_memory,
    changed_paths,
    default_ledger,
    enforce_content_bounds,
    plan_upgrade,
    result_fingerprint,
)
from .upgrade_transaction import (
    InvalidUpgradeTransactionError,
    build_upgrade_transaction,
    serialize_file_entry,
    sha256_text,
    upgrade_transaction_entry_present,
    upgrade_transaction_present,
    validate_upgrade_transaction,
)


BOOTSTRAP_NAMES = ("AGENTS.md", "CLAUDE.md")


class UpgradeBlocked(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _empty_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "installed_package_version": installed_package_version(),
        "workspace_framework_version": None,
        "current_schema_version": None,
        "target_schema_version": TARGET_WORKSPACE_SCHEMA_VERSION,
        "compatibility": "blocked",
        "migration_ids": [],
        "changed_paths": [],
        "transaction_id": None,
        "recovery_action": None,
        "human_action_required": False,
    }
    result.update(overrides)
    return result


def _manifest(project: dict[str, Any]) -> dict[str, Any] | None:
    manifest = project.get("files", {}).get(MANIFEST_PATH)
    if isinstance(manifest, dict) and manifest.get("framework") == "aef":
        return manifest
    return None


def _refuse_unsafe_root(root: Path) -> None:
    if is_link_or_reparse_point(root) or is_link_or_reparse_point(root / ".agent"):
        raise UpgradeBlocked("workspace_path_unsafe")


def _readonly(path: Path) -> bool:
    if not path.exists():
        return False
    if is_link_or_reparse_point(path):
        return True
    if not os.access(path, os.W_OK):
        return True
    try:
        if not (path.stat().st_mode & stat.S_IWRITE):
            return True
    except OSError:
        return True
    return False


def _file_content(value: Any) -> str:
    from .upgrade_transaction import filesystem_json
    if isinstance(value, str):
        return value
    return filesystem_json(value)


def _observe_file(root: Path, rel: str) -> str | None:
    path = root.joinpath(*rel.split("/"))
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    if is_link_or_reparse_point(path):
        raise UpgradeBlocked("workspace_path_unsafe")
    return path.read_text(encoding="utf-8")


def _preflight_paths(root: Path, paths: list[str], current_files: dict[str, Any]) -> None:
    for rel in paths:
        try:
            from .filesystem import _validate_workspace_path
            target = _validate_workspace_path(root, rel)
        except WorkspacePathError as exc:
            raise UpgradeBlocked("workspace_path_unsafe") from exc
        if rel in current_files and _readonly(target):
            raise UpgradeBlocked("managed_file_not_replaceable")
        if target.exists() and is_link_or_reparse_point(target):
            raise UpgradeBlocked("workspace_path_unsafe")


def _preserve_bootstrap(root: Path, desired: dict[str, Any], current: dict[str, Any]) -> None:
    for name in BOOTSTRAP_NAMES:
        path = root / name
        if path.exists():
            continue
        if name in desired.get("files", {}) or name in current.get("files", {}):
            raise UpgradeBlocked("bootstrap_create_forbidden")


def _machine(
    project: dict[str, Any],
    plan: UpgradePlan | None,
    *,
    compatibility: str,
    changed: list[str] | None = None,
    transaction_id: str | None = None,
    recovery_action: str | None = None,
    human_action_required: bool = False,
) -> dict[str, Any]:
    manifest = _manifest(project)
    return _empty_result(
        workspace_framework_version=(
            manifest.get("framework_version") if manifest else None
        ),
        current_schema_version=(
            manifest.get("schema_version") if manifest else (
                plan.current_schema_version if plan else None
            )
        ),
        target_schema_version=(
            plan.target_schema_version if plan else TARGET_WORKSPACE_SCHEMA_VERSION
        ),
        compatibility=compatibility,
        migration_ids=plan.migration_ids if plan else [],
        changed_paths=list(changed or []),
        transaction_id=transaction_id,
        recovery_action=recovery_action,
        human_action_required=human_action_required,
    )


def _project_plan(
    project: dict[str, Any],
    migrations: tuple[MigrationSpec, ...] | list[MigrationSpec],
) -> UpgradePlan:
    manifest = _manifest(project)
    target = TARGET_WORKSPACE_SCHEMA_VERSION
    if manifest is None:
        return plan_upgrade(None, target_schema_version=target, migrations=migrations)
    ledger = default_ledger(project)
    return plan_upgrade(
        manifest.get("schema_version"),
        target_schema_version=target,
        migrations=migrations,
        ledger=ledger,
    )


def _project_desired(project: dict[str, Any], plan: UpgradePlan) -> dict[str, Any]:
    desired = apply_plan_in_memory(project, plan)
    ledger = default_ledger(project)
    fingerprint = result_fingerprint(
        desired.get("files", {}),
        sorted(set(changed_paths(project, desired) + [LEDGER_PATH])),
    )
    # transaction_id filled later; placeholder fingerprint only for ledger bind
    desired_files = desired.setdefault("files", {})
    desired_files[LEDGER_PATH] = append_ledger_entries(
        ledger, plan, transaction_id="pending", result_fingerprint=fingerprint,
    )
    return desired


def _journal_from_states(
    project: dict[str, Any],
    desired: dict[str, Any],
    plan: UpgradePlan,
    paths: list[str],
) -> dict[str, Any]:
    manifest = _manifest(project) or {}
    created = [path for path in paths if path not in project.get("files", {})]
    entries = []
    for path in paths:
        entries.append(serialize_file_entry(
            path,
            project.get("files", {}).get(path),
            desired.get("files", {}).get(path),
            created=path in created,
        ))
    skeleton = {
        "created_paths": sorted(created),
        "files": entries,
        "from_schema_version": plan.current_schema_version or "",
        "migration_ids": plan.migration_ids,
        "paths": [entry["path"] for entry in entries],
        "phase": "prepared",
        "to_schema_version": plan.target_schema_version,
        "workspace_instance_id": str(manifest.get("instance_id") or "unknown"),
    }
    from .upgrade_transaction import compute_transaction_id
    transaction_id = compute_transaction_id(skeleton)
    ledger = desired["files"][LEDGER_PATH]
    for entry in ledger.get("applied", []):
        if entry.get("transaction_id") == "pending":
            entry["transaction_id"] = transaction_id
    for entry in skeleton["files"]:
        if entry["path"] == LEDGER_PATH:
            from .upgrade_transaction import filesystem_json
            after = filesystem_json(desired["files"][LEDGER_PATH])
            entry["after_content"] = after
            entry["after_hash"] = sha256_text(after)
    return build_upgrade_transaction(
        workspace_instance_id=skeleton["workspace_instance_id"],
        from_schema_version=skeleton["from_schema_version"],
        to_schema_version=skeleton["to_schema_version"],
        migration_ids=skeleton["migration_ids"],
        files=skeleton["files"],
        created_paths=skeleton["created_paths"],
        phase="prepared",
    )


def inspect_upgrade(root: str | Path, *, migrations=None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return run_upgrade(root, mode="check", migrations=migrations)


def dry_run_upgrade(root: str | Path, *, migrations=None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return run_upgrade(root, mode="dry_run", migrations=migrations)


def apply_upgrade(root: str | Path, *, migrations=None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return run_upgrade(root, mode="apply", migrations=migrations)


def recover_upgrade(
    root: str | Path, *, dry_run: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return run_upgrade(root, mode="recover_dry_run" if dry_run else "recover")


def run_upgrade(
    root: str | Path,
    *,
    mode: str,
    migrations=None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Return (status, result, extra) where extra has diff/meta/reason."""
    root = Path(root).resolve()
    registry = tuple(ordered_migrations() if migrations is None else migrations)
    extra: dict[str, Any] = {"diff": None, "meta": {}, "reason": None}
    try:
        _refuse_unsafe_root(root)
        if not (root / ".agent").exists() and mode not in {"recover", "recover_dry_run"}:
            extra["reason"] = "workspace_not_initialized"
            extra["meta"] = {"reason": "workspace_not_initialized"}
            return "BLOCKED", _empty_result(human_action_required=False), extra

        project = load_workspace(root)
        if mode in {"recover", "recover_dry_run"}:
            return _recover(root, project, dry_run=mode == "recover_dry_run")

        if upgrade_transaction_present(project) or upgrade_transaction_entry_present(root):
            extra["reason"] = "upgrade_recovery_required"
            extra["meta"] = {"reason": "upgrade_recovery_required"}
            extra["human"] = True
            result = _machine(
                project, None, compatibility="blocked",
                recovery_action="inspect", human_action_required=True,
            )
            return "BLOCKED", result, extra
        if evaluation_recovery_required(project) or (
            EVALUATION_TRANSACTION_PATH in project.get("files", {})
        ):
            extra["reason"] = "evaluation_recovery_required"
            extra["meta"] = {"reason": "evaluation_recovery_required"}
            return "BLOCKED", _machine(project, None, compatibility="blocked"), extra

        plan = _project_plan(project, registry)
        if plan.status == "BLOCKED":
            extra["reason"] = plan.reason
            extra["meta"] = {"reason": plan.reason}
            return "BLOCKED", _machine(project, plan, compatibility="blocked"), extra
        if plan.status == "NO_CHANGE":
            extra["diff"] = {"created": [], "modified": [], "removed": []}
            return "NO_CHANGE", _machine(project, plan, compatibility="current"), extra

        desired = _project_desired(project, plan)
        paths = changed_paths(project, desired)
        if any(path not in set(sum((list(spec.paths) for spec in plan.migrations), [LEDGER_PATH, MANIFEST_PATH])) for path in paths):
            extra["reason"] = "undeclared_path"
            extra["meta"] = {"reason": "undeclared_path"}
            return "BLOCKED", _machine(project, plan, compatibility="blocked"), extra
        bound = enforce_content_bounds(desired.get("files", {}), paths)
        if bound:
            extra["reason"] = bound
            extra["meta"] = {"reason": bound}
            return "BLOCKED", _machine(project, plan, compatibility="blocked"), extra
        _preflight_paths(root, paths, project.get("files", {}))
        _preserve_bootstrap(root, desired, project)
        journal = _journal_from_states(project, desired, plan, sorted(paths))
        result = _machine(
            project, plan, compatibility="upgrade_required",
            changed=paths, transaction_id=journal["transaction_id"],
        )
        extra["diff"] = {
            "created": [path for path in paths if path not in project.get("files", {})],
            "modified": [path for path in paths if path in project.get("files", {})],
            "removed": [],
        }
        if mode in {"check", "dry_run"}:
            return "CHANGE", result, extra
        return _apply_transaction(root, project, desired, journal, result, extra)
    except UpgradeBlocked as exc:
        extra["reason"] = exc.reason
        extra["meta"] = {"reason": exc.reason}
        return "BLOCKED", _empty_result(), extra
    except MigrationFailure as exc:
        extra["reason"] = "migration_failed"
        extra["meta"] = {"reason": "migration_failed", "migration_id": exc.migration_id}
        return "FAILED", _empty_result(), extra


def _apply_transaction(
    root: Path,
    project: dict[str, Any],
    desired: dict[str, Any],
    journal: dict[str, Any],
    result: dict[str, Any],
    extra: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    prepared_project = deepcopy(project)
    prepared_project.setdefault("files", {})[UPGRADE_TRANSACTION_PATH] = journal
    _apply_workspace_unchecked(root, project, prepared_project, allow_delete=False)

    mutating = deepcopy(prepared_project)
    for path, value in desired.get("files", {}).items():
        mutating["files"][path] = deepcopy(value)
    _apply_workspace_unchecked(root, prepared_project, mutating, allow_delete=False)

    for entry in journal["files"]:
        observed = _observe_file(root, entry["path"])
        if observed is None or sha256_text(observed) != entry["after_hash"]:
            extra["reason"] = "hash_mismatch"
            extra["meta"] = {"reason": "hash_mismatch"}
            return "BLOCKED", result, extra

    committed = deepcopy(journal)
    committed["phase"] = "committed"
    committed_project = deepcopy(mutating)
    committed_project["files"][UPGRADE_TRANSACTION_PATH] = committed
    _apply_workspace_unchecked(root, mutating, committed_project, allow_delete=False)

    final = load_workspace(root)
    cleanup = deepcopy(final)
    cleanup.get("files", {}).pop(UPGRADE_TRANSACTION_PATH, None)
    _apply_workspace_unchecked(root, final, cleanup, allow_delete=True)
    extra["diff"] = extra.get("diff") or {
        "created": [], "modified": [], "removed": [],
    }
    return "CHANGE", result, extra


def _recover(
    root: Path, project: dict[str, Any], *, dry_run: bool,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    extra: dict[str, Any] = {"diff": None, "meta": {}, "reason": None}
    raw = project.get("files", {}).get(UPGRADE_TRANSACTION_PATH)
    if raw is None and not upgrade_transaction_entry_present(root):
        extra["diff"] = {"created": [], "modified": [], "removed": []}
        extra["meta"] = {"reason": "no_upgrade_transaction"}
        return "NO_CHANGE", _machine(
            project, None, compatibility="current", recovery_action="none",
        ), extra
    try:
        journal = validate_upgrade_transaction(raw)
    except (InvalidUpgradeTransactionError, TypeError, ValueError):
        extra["reason"] = "invalid_upgrade_transaction"
        extra["meta"] = {"reason": "invalid_upgrade_transaction"}
        return "BLOCKED", _machine(
            project, None, compatibility="blocked",
            recovery_action="inspect", human_action_required=True,
        ), extra

    observations = {}
    for entry in journal["files"]:
        try:
            observations[entry["path"]] = _observe_file(root, entry["path"])
        except UpgradeBlocked as exc:
            extra["reason"] = exc.reason
            extra["meta"] = {"reason": exc.reason}
            return "BLOCKED", _machine(
                project, None, compatibility="blocked",
                recovery_action="inspect", human_action_required=True,
            ), extra

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
    action = None
    if journal["phase"] == "prepared" and before_ok:
        action = "rollback"
    elif journal["phase"] == "committed" and after_ok:
        action = "finalize"
    else:
        extra["reason"] = "upgrade_transaction_inconsistent"
        extra["meta"] = {"reason": "upgrade_transaction_inconsistent"}
        return "BLOCKED", _machine(
            project, None, compatibility="blocked",
            transaction_id=journal["transaction_id"],
            recovery_action="inspect", human_action_required=True,
        ), extra

    result = _machine(
        project, None, compatibility="blocked",
        transaction_id=journal["transaction_id"],
        recovery_action=action, human_action_required=False,
    )
    if dry_run:
        extra["diff"] = {"created": [], "modified": [], "removed": []}
        return "CHANGE", result, extra

    if action == "rollback":
        restored = deepcopy(project)
        for entry in journal["files"]:
            if entry["path"] in journal["created_paths"]:
                restored.get("files", {}).pop(entry["path"], None)
            else:
                from .strict_json import validate_strict_json
                import json as _json
                try:
                    parsed = _json.loads(entry["before_content"])
                    validate_strict_json(parsed)
                    restored.setdefault("files", {})[entry["path"]] = parsed
                except Exception:
                    restored.setdefault("files", {})[entry["path"]] = entry["before_content"]
        restored.get("files", {}).pop(UPGRADE_TRANSACTION_PATH, None)
        _apply_workspace_unchecked(root, project, restored, allow_delete=True)
    else:
        cleanup = deepcopy(project)
        cleanup.get("files", {}).pop(UPGRADE_TRANSACTION_PATH, None)
        _apply_workspace_unchecked(root, project, cleanup, allow_delete=True)
    extra["diff"] = {
        "created": [],
        "modified": [],
        "removed": [UPGRADE_TRANSACTION_PATH],
    }
    return "CHANGE", result, extra


def audit_upgrade_findings(project: dict[str, Any], root: str | Path | None = None) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    files = project.get("files", {})
    present = UPGRADE_TRANSACTION_PATH in files
    if root is not None and upgrade_transaction_entry_present(root):
        present = True
    if not present:
        manifest = _manifest(project)
        if manifest and manifest.get("schema_version") not in {
            TARGET_WORKSPACE_SCHEMA_VERSION,
        }:
            findings.append({"id": "unsupported-schema-version", "severity": "error"})
        return findings

    findings.append({"id": "upgrade-recovery-required", "severity": "error"})
    raw = files.get(UPGRADE_TRANSACTION_PATH)
    try:
        journal = validate_upgrade_transaction(raw)
    except (InvalidUpgradeTransactionError, TypeError, ValueError):
        findings.append({"id": "upgrade-journal-malformed", "severity": "error"})
        return findings

    if root is not None:
        before_hits = 0
        after_hits = 0
        for entry in journal["files"]:
            path = Path(root).joinpath(*entry["path"].split("/"))
            if not path.exists():
                if entry["path"] in journal["created_paths"]:
                    before_hits += 1
                continue
            digest = sha256_text(path.read_text(encoding="utf-8"))
            if digest == entry["before_hash"]:
                before_hits += 1
            elif digest == entry["after_hash"]:
                after_hits += 1
            else:
                findings.append({"id": "upgrade-hash-mismatch", "severity": "error"})
        if before_hits and after_hits:
            findings.append({"id": "upgrade-partial-state", "severity": "error"})
    return findings
