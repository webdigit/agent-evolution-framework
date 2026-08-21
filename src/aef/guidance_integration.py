"""Managed guidance segments — AGENTS commun + doorbells (no I/O)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .claude_integration import (
    CORE_DOCTRINE_PATHS,
    inspect_managed_bridge,
    validate_claude_integration_workspace,
)
from .transaction_guard import mutation_guard_metadata


GUIDANCE_VERSION = "1.0.0"

AGENTS_PATH = "AGENTS.md"
CLAUDE_ROOT_PATH = "CLAUDE.md"
GEMINI_PATH = "GEMINI.md"
LEGACY_CLAUDE_PATH = ".claude/CLAUDE.md"

AGENTS_BEGIN_PREFIX = b"<!-- AEF:AGENTS:BEGIN version="
AGENTS_END_MARKER = b"<!-- AEF:AGENTS:END -->"
CLAUDE_ROOT_BEGIN_PREFIX = b"<!-- AEF:CLAUDE-ROOT:BEGIN version="
CLAUDE_ROOT_END_MARKER = b"<!-- AEF:CLAUDE-ROOT:END -->"
GEMINI_BEGIN_PREFIX = b"<!-- AEF:GEMINI:BEGIN version="
GEMINI_END_MARKER = b"<!-- AEF:GEMINI:END -->"

AGENTS_SEGMENT = """<!-- AEF:AGENTS:BEGIN version=\"1.0.0\" -->
# AEF Agent Guidance

Read and follow the project doctrine files:

- `.agent/core/constitution.md`
- `.agent/core/autonomy.md`
- `.agent/core/learning.md`
- `.agent/core/levels.md`
- `.agent/core/scoring.md`

Python runtime for this project: see `docs/runtime.md`. Prefer `aef doctor` before AEF transitions. Use `aef record` only when the operator asks to declare facts.

This file is guidance only. It is not permission, authority, or technical enforcement.
<!-- AEF:AGENTS:END -->
"""

CLAUDE_ROOT_SEGMENT = """<!-- AEF:CLAUDE-ROOT:BEGIN version=\"1.0.0\" -->
@AGENTS.md
<!-- AEF:CLAUDE-ROOT:END -->
"""

GEMINI_SEGMENT = """<!-- AEF:GEMINI:BEGIN version=\"1.0.0\" -->
Read and follow AGENTS.md in this project. It is guidance only, not permission.
<!-- AEF:GEMINI:END -->
"""

AGENTS_BYTES = AGENTS_SEGMENT.encode("utf-8")
CLAUDE_ROOT_BYTES = CLAUDE_ROOT_SEGMENT.encode("utf-8")
GEMINI_BYTES = GEMINI_SEGMENT.encode("utf-8")

DOOR_SPECS = {
    "agents": {
        "path": AGENTS_PATH,
        "begin_prefix": AGENTS_BEGIN_PREFIX,
        "end_marker": AGENTS_END_MARKER,
        "catalog": {GUIDANCE_VERSION: AGENTS_BYTES},
        "bytes": AGENTS_BYTES,
    },
    "claude": {
        "path": CLAUDE_ROOT_PATH,
        "begin_prefix": CLAUDE_ROOT_BEGIN_PREFIX,
        "end_marker": CLAUDE_ROOT_END_MARKER,
        "catalog": {GUIDANCE_VERSION: CLAUDE_ROOT_BYTES},
        "bytes": CLAUDE_ROOT_BYTES,
    },
    "gemini": {
        "path": GEMINI_PATH,
        "begin_prefix": GEMINI_BEGIN_PREFIX,
        "end_marker": GEMINI_END_MARKER,
        "catalog": {GUIDANCE_VERSION: GEMINI_BYTES},
        "bytes": GEMINI_BYTES,
    },
}


def inspect_managed_segment(
    existing: bytes | None,
    *,
    begin_prefix: bytes,
    end_marker: bytes,
    catalog: dict[str, bytes],
) -> dict[str, Any]:
    """Classify one managed segment without normalizing surrounding bytes."""
    if existing is None or existing == b"":
        return {"state": "absent", "version": None, "start": None, "end": None}
    begin_count = existing.count(begin_prefix)
    end_count = existing.count(end_marker)
    if begin_count == 0 and end_count == 0:
        return {"state": "absent", "version": None, "start": None, "end": None}
    if begin_count != 1 or end_count != 1:
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    begin = existing.index(begin_prefix)
    end_pos = existing.index(end_marker)
    if end_pos < begin:
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    line_end = existing.find(b" -->", begin)
    if line_end < 0 or line_end > existing.find(b"\n", begin):
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    version_raw = existing[begin + len(begin_prefix):line_end]
    if len(version_raw) < 2 or version_raw[:1] != b'"' or version_raw[-1:] != b'"':
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    try:
        version = version_raw[1:-1].decode("ascii")
    except UnicodeDecodeError:
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    body_end = end_pos + len(end_marker)
    if existing[body_end:body_end + 1] == b"\n":
        body_end += 1
    start = begin
    if begin >= 2 and existing[begin - 2:begin] == b"\n\n":
        start = begin - 2
    expected = catalog.get(version)
    if expected is None:
        state = "unsupported_version"
    elif existing[begin:body_end] != expected:
        state = "modified"
    else:
        state = "installed"
    return {"state": state, "version": version, "start": start, "end": body_end}


def inspect_door(door: str, existing: bytes | None) -> dict[str, Any]:
    spec = DOOR_SPECS[door]
    return inspect_managed_segment(
        existing,
        begin_prefix=spec["begin_prefix"],
        end_marker=spec["end_marker"],
        catalog=spec["catalog"],
    )


def _blocked(project, reason, *, door=None, bridge=None, extra=None):
    payload = {
        "reason": reason,
        "scope": "project",
        "door": door,
        "bridge": bridge,
        "integration_version": None,
        "doctrine_files": 0,
        "enforcement": "guidance_only",
    }
    if extra:
        payload.update(extra)
    return "BLOCKED", deepcopy(project), payload


def plan_door_integration(
    project: dict[str, Any],
    existing: bytes | None,
    *,
    door: str,
    remove: bool = False,
    status_only: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Plan one guidance door without I/O."""
    if door not in DOOR_SPECS:
        raise ValueError(f"unsupported guidance door: {door}")
    spec = DOOR_SPECS[door]
    workspace_reason = validate_claude_integration_workspace(project)
    inspection = inspect_door(door, existing)
    base = {
        "scope": "project",
        "door": door,
        "bridge_path": spec["path"],
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
        reason_map = {
            "absent": f"{door}_integration_not_installed",
            "ambiguous": f"ambiguous_{door}_managed_block",
            "modified": f"modified_{door}_managed_block",
            "unsupported_version": f"unsupported_{door}_integration_version",
        }
        return status, deepcopy(project), {
            **base,
            "reason": None if healthy else (workspace_reason or reason_map[inspection["state"]]),
            "bridge_healthy": inspection["state"] == "installed",
            "workspace_compatible": workspace_reason is None,
        }
    if workspace_reason is not None:
        return _blocked(project, workspace_reason, door=door, bridge=inspection)
    guard = mutation_guard_metadata(project)
    if guard is not None:
        return _blocked(project, guard["reason"], door=door, bridge=inspection)
    if inspection["state"] in {"ambiguous", "modified", "unsupported_version"}:
        reason = {
            "ambiguous": f"ambiguous_{door}_managed_block",
            "modified": f"modified_{door}_managed_block",
            "unsupported_version": f"unsupported_{door}_integration_version",
        }[inspection["state"]]
        return _blocked(project, reason, door=door, bridge=inspection)
    if remove:
        if inspection["state"] == "absent":
            return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
        desired = existing[: inspection["start"]] + existing[inspection["end"]:]
        return "CHANGE", deepcopy(project), {
            **base, "reason": None, "desired_bytes": desired,
            "integration_version": GUIDANCE_VERSION,
        }
    if inspection["state"] == "installed":
        return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
    if not existing:
        desired = spec["bytes"]
    else:
        desired = existing + b"\n\n" + spec["bytes"]
    return "CHANGE", deepcopy(project), {
        **base, "reason": None, "desired_bytes": desired,
        "integration_version": GUIDANCE_VERSION,
    }


def plan_claude_door(
    project: dict[str, Any],
    root_existing: bytes | None,
    legacy_existing: bytes | None,
    *,
    remove: bool = False,
    status_only: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Claude door: root doorbell + legacy .claude status/remove fallback."""
    legacy = inspect_managed_bridge(legacy_existing)
    if status_only:
        status, _, meta = plan_door_integration(
            project, root_existing, door="claude", status_only=True,
        )
        meta = {
            **meta,
            "legacy_bridge": legacy,
            "legacy_path": LEGACY_CLAUDE_PATH,
        }
        root_state = meta["bridge"]["state"]
        # Brownfield: legacy installed alone is a healthy Claude guidance presence.
        if (
            meta.get("workspace_compatible")
            and root_state == "absent"
            and legacy["state"] == "installed"
        ):
            meta["bridge_healthy"] = True
            meta["reason"] = None
            meta["installed_via"] = "legacy_bridge"
            return "NO_CHANGE", deepcopy(project), meta
        if root_state == "installed":
            meta["installed_via"] = "doorbell"
        elif legacy["state"] not in {None, "absent"}:
            meta["installed_via"] = "legacy_bridge"
        else:
            meta["installed_via"] = None
        # Legacy modified/ambiguous does not BLOCK root status unless root also bad.
        if legacy["state"] in {"ambiguous", "modified", "unsupported_version"}:
            meta.setdefault("warnings", [])
            if isinstance(meta.get("warnings"), list):
                meta["warnings"] = list(meta.get("warnings") or [])
            # keep status from root planning
        return status, deepcopy(project), meta

    if remove:
        root_insp = inspect_door("claude", root_existing)
        if root_insp["state"] != "absent":
            return plan_door_integration(
                project, root_existing, door="claude", remove=True,
            )
        # Fallback: remove legacy bridge only when root doorbell absent.
        from .claude_integration import plan_claude_integration
        status, project_out, meta = plan_claude_integration(
            project, legacy_existing, remove=True, status_only=False,
        )
        meta = {
            **meta,
            "door": "claude",
            "legacy_bridge": legacy,
            "target": "legacy_bridge",
        }
        return status, project_out, meta

    # Install root doorbell only — never mutate legacy .claude bridge.
    status, project_out, meta = plan_door_integration(
        project, root_existing, door="claude", remove=False, status_only=False,
    )
    meta = {
        **meta,
        "legacy_bridge": legacy,
        "legacy_path": LEGACY_CLAUDE_PATH,
        "target": "doorbell",
    }
    return status, project_out, meta


def doors_for_integration(name: str) -> list[str]:
    if name == "all":
        return ["agents", "claude", "gemini"]
    if name in {"agents", "claude", "gemini"}:
        return [name]
    raise ValueError(f"unsupported integration: {name}")
