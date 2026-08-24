from __future__ import annotations

from copy import deepcopy

from .markdown_code import classify_managed_markers, place_managed_segment
from .transaction_guard import mutation_guard_metadata


CLAUDE_INTEGRATION_VERSION = "1.0.0"
CLAUDE_BRIDGE_PATH = ".claude/CLAUDE.md"
CORE_DOCTRINE_PATHS = (
    ".agent/core/constitution.md",
    ".agent/core/autonomy.md",
    ".agent/core/learning.md",
    ".agent/core/levels.md",
    ".agent/core/scoring.md",
)
BEGIN_PREFIX = b"<!-- AEF:CLAUDE-PROJECT:BEGIN version="
END_MARKER = b"<!-- AEF:CLAUDE-PROJECT:END -->"

CLAUDE_BRIDGE = """<!-- AEF:CLAUDE-PROJECT:BEGIN version=\"1.0.0\" -->
# AEF Project Guidance

@../.agent/core/constitution.md
@../.agent/core/autonomy.md
@../.agent/core/learning.md
@../.agent/core/levels.md
@../.agent/core/scoring.md

- AEF applies only to this project. Do not infer AEF state, policy, authority, or doctrine from a parent directory, a user directory, or another project.
- Read and follow the imported AEF doctrine as project guidance.
- Treat Python policies, persisted JSON state, and approved hooks as the executable authorities. This file is guidance, not complete technical enforcement.
- Never present learned knowledge, a recommendation, a competency level, XP, or Trust as permission to perform an action.
- When an AEF result reports new promotion recommendations, notify the user.
- When review is required, use `aef evaluate --list` for human-readable consultation or `aef --json evaluate --list` for automation.
- Never approve, reject, refresh, or recover an evaluation on the user's behalf.
- If AEF reports that evaluation recovery is required, treat all affected levels as unavailable, notify the user, and do not perform recovery without an explicit user request.
- Use explicit `--json` output for automation. Preserve human output when presenting results to the user.
- Do not claim that AEF technically controls every Claude action unless an executable integration explicitly enforces that action.
<!-- AEF:CLAUDE-PROJECT:END -->
"""
CLAUDE_BRIDGE_BYTES = CLAUDE_BRIDGE.encode("utf-8")
LEGACY_CLAUDE_BRIDGE_BYTES = CLAUDE_BRIDGE_BYTES.replace(
    b'version="1.0.0"', b'version="0.9.0"', 1
)
BRIDGE_CATALOG = {
    "0.9.0": LEGACY_CLAUDE_BRIDGE_BYTES,
    CLAUDE_INTEGRATION_VERSION: CLAUDE_BRIDGE_BYTES,
}


def _blocked(project, reason, *, bridge=None):
    return "BLOCKED", deepcopy(project), {
        "reason": reason,
        "scope": "project",
        "bridge": bridge,
        "integration_version": None,
        "doctrine_files": 0,
        "enforcement": "guidance_only",
    }


def validate_claude_integration_workspace(project):
    """Validate only persisted state required by the project bridge."""
    files = project.get("files") if isinstance(project, dict) else None
    manifest = files.get(".agent/manifest.json") if isinstance(files, dict) else None
    if not isinstance(manifest, dict) or manifest.get("framework") != "aef":
        return "workspace_not_initialized"
    if manifest.get("framework_version") != "1.0.0":
        return "framework_version_mismatch"
    if manifest.get("schema_version") != "1.0.0":
        return "schema_version_mismatch"
    for path in CORE_DOCTRINE_PATHS:
        value = files.get(path)
        if not isinstance(value, str) or not value.strip():
            return "missing_aef_doctrine"
    return None


def inspect_managed_bridge(existing):
    """Classify one bridge without normalizing any surrounding bytes."""
    return classify_managed_markers(
        existing,
        begin_prefix=BEGIN_PREFIX,
        end_marker=END_MARKER,
        catalog=BRIDGE_CATALOG,
    )


def plan_claude_integration(project, existing, *, remove=False, status_only=False):
    """Return a deterministic project-local bridge plan without performing I/O."""
    workspace_reason = validate_claude_integration_workspace(project)
    inspection = inspect_managed_bridge(existing)
    base = {
        "scope": "project",
        "bridge_path": CLAUDE_BRIDGE_PATH,
        "bridge": inspection,
        "integration_version": inspection.get("version"),
        "doctrine_files": len(CORE_DOCTRINE_PATHS) if workspace_reason is None else 0,
        "enforcement": "guidance_only",
        "desired_bytes": existing,
        "workspace_reason": workspace_reason,
    }
    if status_only:
        healthy = workspace_reason is None and inspection["state"] == "installed"
        status = (
            "BLOCKED" if workspace_reason is not None or inspection["state"] in {
                "ambiguous", "modified", "unsupported_version",
            } else "NO_CHANGE"
        )
        return status, deepcopy(project), {
            **base, "reason": None if healthy else (
                workspace_reason or {
                    "absent": "claude_integration_not_installed",
                    "ambiguous": "ambiguous_claude_managed_block",
                    "modified": "modified_claude_managed_block",
                    "unsupported_version": "unsupported_claude_integration_version",
                }[inspection["state"]]
            ), "bridge_healthy": inspection["state"] == "installed",
            "workspace_compatible": workspace_reason is None,
        }
    if workspace_reason is not None:
        return _blocked(project, workspace_reason, bridge=inspection)
    guard = mutation_guard_metadata(project)
    if guard is not None:
        return _blocked(project, guard["reason"], bridge=inspection)
    if inspection["state"] in {"ambiguous", "modified", "unsupported_version"}:
        reason = {
            "ambiguous": "ambiguous_claude_managed_block",
            "modified": "modified_claude_managed_block",
            "unsupported_version": "unsupported_claude_integration_version",
        }[inspection["state"]]
        return _blocked(project, reason, bridge=inspection)
    if remove:
        if inspection["state"] == "absent":
            return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
        desired = existing[:inspection["start"]] + existing[inspection["end"]:]
        return "CHANGE", deepcopy(project), {
            **base, "reason": None, "desired_bytes": desired,
            "integration_version": CLAUDE_INTEGRATION_VERSION,
        }
    if (
        inspection["state"] == "installed"
        and inspection["version"] == CLAUDE_INTEGRATION_VERSION
    ):
        return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
    if inspection["state"] == "installed":
        prefix = b"\n\n" if inspection["start"] < existing.index(BEGIN_PREFIX) else b""
        desired = (
            existing[:inspection["start"]] + prefix + CLAUDE_BRIDGE_BYTES
            + existing[inspection["end"]:]
        )
        return "CHANGE", deepcopy(project), {
            **base, "reason": None, "desired_bytes": desired,
            "integration_version": CLAUDE_INTEGRATION_VERSION,
        }
    desired = place_managed_segment(existing, CLAUDE_BRIDGE_BYTES)
    return "CHANGE", deepcopy(project), {
        **base, "reason": None, "desired_bytes": desired,
        "integration_version": CLAUDE_INTEGRATION_VERSION,
    }
