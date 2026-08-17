import hashlib
import json
from copy import deepcopy


def content_checksum(value):
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def capture_managed_inventory(files, managed_paths):
    return {
        path: content_checksum(files[path])
        for path in sorted(set(managed_paths)) if path in files
    }


def detect_managed_drift(files, inventory):
    findings = []
    for path, expected in sorted(inventory.items()):
        if path not in files:
            findings.append({"path": path, "status": "missing"})
            continue
        actual = content_checksum(files[path])
        if actual != expected:
            findings.append({"path": path, "status": "locally_modified", "expected": expected, "actual": actual})
    return findings


def reconcile_framework_file(files, inventory, *, path, desired_content):
    """Update a framework-managed file only when it has not drifted locally."""
    out_files = deepcopy(files)
    out_inventory = deepcopy(inventory)
    if path in out_inventory:
        drift = detect_managed_drift(out_files, {path: out_inventory[path]})
        if drift:
            return "BLOCKED", out_files, out_inventory, drift[0]
    if out_files.get(path) == desired_content:
        out_inventory[path] = content_checksum(desired_content)
        return "NO_CHANGE", out_files, out_inventory, None
    out_files[path] = deepcopy(desired_content)
    out_inventory[path] = content_checksum(desired_content)
    return "CHANGE", out_files, out_inventory, None
