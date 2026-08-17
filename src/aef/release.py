from copy import deepcopy
from .operations import upgrade_project
from .ownership import reconcile_framework_file


def apply_framework_release(project, *, target_version, migrations, managed_updates=None):
    """Reference release orchestration for the lab.

    Schema migrations are preflighted/applied first on a copy. Framework-managed
    file updates are then reconciled against the stored inventory. A local edit
    blocks the release and returns the last safe pre-release project, avoiding a
    partially committed framework release.
    """
    source = deepcopy(project)
    status, upgraded, meta = upgrade_project(source, target_version=target_version, migrations=migrations)
    if status in {"BLOCKED", "FAILED"}:
        return status, source, {"phase": "schema", **meta}

    managed_updates = managed_updates or {}
    files = deepcopy(upgraded.get("files", {}))
    manifest = deepcopy(files.get(".agent/manifest.json", {}))
    inventory = deepcopy(manifest.get("managed_inventory", {}))
    changed_paths = []

    for path, desired in sorted(managed_updates.items()):
        fstatus, files, inventory, finding = reconcile_framework_file(
            files, inventory, path=path, desired_content=desired
        )
        if fstatus == "BLOCKED":
            return "BLOCKED", source, {
                "phase": "managed_files",
                "reason": "managed_file_drift",
                "path": path,
                "finding": finding,
                "schema_applied_in_discarded_plan": meta.get("applied", []),
            }
        if fstatus == "CHANGE":
            changed_paths.append(path)

    files[".agent/manifest.json"]["managed_inventory"] = inventory
    upgraded["files"] = files
    changed = status == "CHANGE" or bool(changed_paths)
    return ("CHANGE" if changed else "NO_CHANGE"), upgraded, {
        "phase": "complete", "applied": meta.get("applied", []), "managed_paths_changed": changed_paths
    }
