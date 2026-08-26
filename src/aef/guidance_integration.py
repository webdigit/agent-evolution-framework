"""Managed guidance segments — AGENTS commun + doorbells + runtime card (no I/O)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .claude_integration import (
    CORE_DOCTRINE_PATHS,
    inspect_managed_bridge,
    validate_claude_integration_workspace,
)
from .markdown_code import classify_managed_markers, place_managed_segment
from .learning_card import LEARNING_CARD_VERSION
from .runtime_doctor import RUNTIME_CARD_VERSION
from .transaction_guard import mutation_guard_metadata


GUIDANCE_VERSION = "1.2.0"

AGENTS_PATH = "AGENTS.md"
CLAUDE_ROOT_PATH = "CLAUDE.md"
GEMINI_PATH = "GEMINI.md"
RUNTIME_PATH = "docs/runtime.md"
LEARNING_PATH = "docs/knowledge.md"
LEGACY_CLAUDE_PATH = ".claude/CLAUDE.md"

AGENTS_BEGIN_PREFIX = b"<!-- AEF:AGENTS:BEGIN version="
AGENTS_END_MARKER = b"<!-- AEF:AGENTS:END -->"
CLAUDE_ROOT_BEGIN_PREFIX = b"<!-- AEF:CLAUDE-ROOT:BEGIN version="
CLAUDE_ROOT_END_MARKER = b"<!-- AEF:CLAUDE-ROOT:END -->"
GEMINI_BEGIN_PREFIX = b"<!-- AEF:GEMINI:BEGIN version="
GEMINI_END_MARKER = b"<!-- AEF:GEMINI:END -->"
RUNTIME_BEGIN_PREFIX = b"<!-- AEF:RUNTIME:BEGIN version="
RUNTIME_END_MARKER = b"<!-- AEF:RUNTIME:END -->"
LEARNING_BEGIN_PREFIX = b"<!-- AEF:LEARNING:BEGIN version="
LEARNING_END_MARKER = b"<!-- AEF:LEARNING:END -->"

AGENTS_SEGMENT_1_0_0 = """<!-- AEF:AGENTS:BEGIN version=\"1.0.0\" -->
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

AGENTS_SEGMENT = """<!-- AEF:AGENTS:BEGIN version=\"1.1.0\" -->
# AEF Agent Guidance

Read and follow the project doctrine files:

- `.agent/core/constitution.md`
- `.agent/core/autonomy.md`
- `.agent/core/learning.md`
- `.agent/core/levels.md`
- `.agent/core/scoring.md`

Python runtime for this project: see `docs/runtime.md`. Produce or refresh that map with `aef integrate runtime` (pure snapshot of `aef doctor`; when périmé/stale, regenerate — not catalog tampering). Prefer `aef doctor` before AEF transitions. Use `aef record` only when the operator asks to declare facts.

This file is guidance only. It is not permission, authority, or technical enforcement.
<!-- AEF:AGENTS:END -->
"""

AGENTS_SEGMENT_1_2_0 = """<!-- AEF:AGENTS:BEGIN version=\"1.2.0\" -->
# AEF Agent Guidance

Read and follow the project doctrine files:

- `.agent/core/constitution.md`
- `.agent/core/autonomy.md`
- `.agent/core/learning.md`
- `.agent/core/levels.md`
- `.agent/core/scoring.md`

Python runtime for this project: see `docs/runtime.md`. Produce or refresh that map with `aef integrate runtime` (pure snapshot of `aef doctor`; when périmé/stale, regenerate — not catalog tampering). Prefer `aef doctor` before AEF transitions.

Learned operational rules (when present): see `docs/knowledge.md`. Produce or refresh with `aef integrate learning` (pure snapshot of persisted knowledge; when périmé/stale, regenerate — not catalog tampering). These rules are derived from declared events, not verified facts — they do not override doctrine.

Use `aef record` only when the operator asks to declare facts.

This file is guidance only. It is not permission, authority, or technical enforcement.
<!-- AEF:AGENTS:END -->
"""

AGENTS_SEGMENT_CURRENT = AGENTS_SEGMENT_1_2_0

CLAUDE_ROOT_SEGMENT_1_0_0 = """<!-- AEF:CLAUDE-ROOT:BEGIN version=\"1.0.0\" -->
@AGENTS.md
<!-- AEF:CLAUDE-ROOT:END -->
"""

CLAUDE_ROOT_SEGMENT = """<!-- AEF:CLAUDE-ROOT:BEGIN version=\"1.1.0\" -->
@AGENTS.md
<!-- AEF:CLAUDE-ROOT:END -->
"""

CLAUDE_ROOT_SEGMENT_1_2_0 = """<!-- AEF:CLAUDE-ROOT:BEGIN version=\"1.2.0\" -->
@AGENTS.md
<!-- AEF:CLAUDE-ROOT:END -->
"""

GEMINI_SEGMENT_1_0_0 = """<!-- AEF:GEMINI:BEGIN version=\"1.0.0\" -->
Read and follow AGENTS.md in this project. It is guidance only, not permission.
<!-- AEF:GEMINI:END -->
"""

GEMINI_SEGMENT = """<!-- AEF:GEMINI:BEGIN version=\"1.1.0\" -->
Read and follow AGENTS.md in this project. It is guidance only, not permission.
<!-- AEF:GEMINI:END -->
"""

GEMINI_SEGMENT_1_2_0 = """<!-- AEF:GEMINI:BEGIN version=\"1.2.0\" -->
Read and follow AGENTS.md in this project. It is guidance only, not permission.
<!-- AEF:GEMINI:END -->
"""

AGENTS_BYTES_1_0_0 = AGENTS_SEGMENT_1_0_0.encode("utf-8")
AGENTS_BYTES_1_1_0 = AGENTS_SEGMENT.encode("utf-8")
AGENTS_BYTES = AGENTS_SEGMENT_CURRENT.encode("utf-8")
CLAUDE_ROOT_BYTES_1_0_0 = CLAUDE_ROOT_SEGMENT_1_0_0.encode("utf-8")
CLAUDE_ROOT_BYTES_1_1_0 = CLAUDE_ROOT_SEGMENT.encode("utf-8")
CLAUDE_ROOT_BYTES = CLAUDE_ROOT_SEGMENT_1_2_0.encode("utf-8")
GEMINI_BYTES_1_0_0 = GEMINI_SEGMENT_1_0_0.encode("utf-8")
GEMINI_BYTES_1_1_0 = GEMINI_SEGMENT.encode("utf-8")
GEMINI_BYTES = GEMINI_SEGMENT_1_2_0.encode("utf-8")

AGENTS_CATALOG = {
    "1.0.0": AGENTS_BYTES_1_0_0,
    "1.1.0": AGENTS_BYTES_1_1_0,
    "1.2.0": AGENTS_BYTES,
}
CLAUDE_ROOT_CATALOG = {
    "1.0.0": CLAUDE_ROOT_BYTES_1_0_0,
    "1.1.0": CLAUDE_ROOT_BYTES_1_1_0,
    "1.2.0": CLAUDE_ROOT_BYTES,
}
GEMINI_CATALOG = {
    "1.0.0": GEMINI_BYTES_1_0_0,
    "1.1.0": GEMINI_BYTES_1_1_0,
    "1.2.0": GEMINI_BYTES,
}

DOOR_SPECS = {
    "agents": {
        "path": AGENTS_PATH,
        "begin_prefix": AGENTS_BEGIN_PREFIX,
        "end_marker": AGENTS_END_MARKER,
        "catalog": AGENTS_CATALOG,
        "bytes": AGENTS_BYTES,
    },
    "claude": {
        "path": CLAUDE_ROOT_PATH,
        "begin_prefix": CLAUDE_ROOT_BEGIN_PREFIX,
        "end_marker": CLAUDE_ROOT_END_MARKER,
        "catalog": CLAUDE_ROOT_CATALOG,
        "bytes": CLAUDE_ROOT_BYTES,
    },
    "gemini": {
        "path": GEMINI_PATH,
        "begin_prefix": GEMINI_BEGIN_PREFIX,
        "end_marker": GEMINI_END_MARKER,
        "catalog": GEMINI_CATALOG,
        "bytes": GEMINI_BYTES,
    },
    "runtime": {
        "path": RUNTIME_PATH,
        "begin_prefix": RUNTIME_BEGIN_PREFIX,
        "end_marker": RUNTIME_END_MARKER,
        # Host-dependent card: catalog is supplied per call from doctor render.
        "catalog": {},
        "bytes": b"",
        "dynamic": True,
    },
    "learning": {
        "path": LEARNING_PATH,
        "begin_prefix": LEARNING_BEGIN_PREFIX,
        "end_marker": LEARNING_END_MARKER,
        # Knowledge-dependent card: catalog is supplied per call from learning render.
        "catalog": {},
        "bytes": b"",
        "dynamic": True,
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
    return classify_managed_markers(
        existing,
        begin_prefix=begin_prefix,
        end_marker=end_marker,
        catalog=catalog,
    )


def inspect_door(door: str, existing: bytes | None) -> dict[str, Any]:
    if door in {"runtime", "learning"}:
        raise ValueError(
            f"{door} door requires expected_bytes; use inspect_{door}_door"
        )
    spec = DOOR_SPECS[door]
    return inspect_managed_segment(
        existing,
        begin_prefix=spec["begin_prefix"],
        end_marker=spec["end_marker"],
        catalog=spec["catalog"],
    )


def inspect_runtime_door(
    existing: bytes | None,
    expected_bytes: bytes,
) -> dict[str, Any]:
    """Classify the runtime card; catalog mismatch is stale, never modified."""
    inspection = classify_managed_markers(
        existing,
        begin_prefix=RUNTIME_BEGIN_PREFIX,
        end_marker=RUNTIME_END_MARKER,
        catalog={RUNTIME_CARD_VERSION: expected_bytes},
    )
    if inspection["state"] == "modified":
        return {**inspection, "state": "stale", "freshness": "stale"}
    if inspection["state"] == "installed":
        return {**inspection, "freshness": "current"}
    return {**inspection, "freshness": None}


def inspect_learning_door(
    existing: bytes | None,
    expected_bytes: bytes,
) -> dict[str, Any]:
    """Classify the learning card; catalog mismatch is stale, never modified."""
    inspection = classify_managed_markers(
        existing,
        begin_prefix=LEARNING_BEGIN_PREFIX,
        end_marker=LEARNING_END_MARKER,
        catalog={LEARNING_CARD_VERSION: expected_bytes},
    )
    if inspection["state"] == "modified":
        return {**inspection, "state": "stale", "freshness": "stale"}
    if inspection["state"] == "installed":
        return {**inspection, "freshness": "current"}
    return {**inspection, "freshness": None}


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
    """Plan one catalog guidance door without I/O."""
    if door not in DOOR_SPECS:
        raise ValueError(f"unsupported guidance door: {door}")
    if DOOR_SPECS[door].get("dynamic"):
        raise ValueError(
            f"door {door!r} is state-dependent; use plan_runtime_integration "
            "or plan_learning_integration"
        )
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
        # Older catalog entry still matches → upgrade to current GUIDANCE_VERSION.
        if inspection.get("version") != GUIDANCE_VERSION:
            stripped = existing[: inspection["start"]] + existing[inspection["end"]:]
            desired = place_managed_segment(
                stripped if stripped else None, spec["bytes"],
            )
            return "CHANGE", deepcopy(project), {
                **base, "reason": None, "desired_bytes": desired,
                "integration_version": GUIDANCE_VERSION,
            }
        return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
    desired = place_managed_segment(existing, spec["bytes"])
    return "CHANGE", deepcopy(project), {
        **base, "reason": None, "desired_bytes": desired,
        "integration_version": GUIDANCE_VERSION,
    }


def plan_runtime_integration(
    project: dict[str, Any],
    existing: bytes | None,
    *,
    expected_bytes: bytes,
    remove: bool = False,
    status_only: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Plan the host-dependent runtime card door (stale ≠ modified)."""
    workspace_reason = validate_claude_integration_workspace(project)
    inspection = inspect_runtime_door(existing, expected_bytes)
    base = {
        "scope": "project",
        "door": "runtime",
        "bridge_path": RUNTIME_PATH,
        "bridge": inspection,
        "integration_version": inspection.get("version"),
        "doctrine_files": len(CORE_DOCTRINE_PATHS) if workspace_reason is None else 0,
        "enforcement": "guidance_only",
        "desired_bytes": existing,
        "workspace_reason": workspace_reason,
        "freshness": inspection.get("freshness"),
    }
    if status_only:
        state = inspection["state"]
        if workspace_reason is not None:
            return "BLOCKED", deepcopy(project), {
                **base,
                "reason": workspace_reason,
                "bridge_healthy": False,
                "workspace_compatible": False,
            }
        if state in {"ambiguous", "unsupported_version"}:
            reason = {
                "ambiguous": "ambiguous_runtime_managed_block",
                "unsupported_version": "unsupported_runtime_integration_version",
            }[state]
            return "BLOCKED", deepcopy(project), {
                **base,
                "reason": reason,
                "bridge_healthy": False,
                "workspace_compatible": True,
            }
        if state == "stale":
            return "NO_CHANGE", deepcopy(project), {
                **base,
                "reason": "runtime_card_stale",
                "bridge_healthy": False,
                "workspace_compatible": True,
                "freshness": "stale",
            }
        if state == "absent":
            return "NO_CHANGE", deepcopy(project), {
                **base,
                "reason": "runtime_integration_not_installed",
                "bridge_healthy": False,
                "workspace_compatible": True,
            }
        return "NO_CHANGE", deepcopy(project), {
            **base,
            "reason": None,
            "bridge_healthy": True,
            "workspace_compatible": True,
            "freshness": "current",
        }
    if workspace_reason is not None:
        return _blocked(project, workspace_reason, door="runtime", bridge=inspection)
    guard = mutation_guard_metadata(project)
    if guard is not None:
        return _blocked(project, guard["reason"], door="runtime", bridge=inspection)
    if inspection["state"] in {"ambiguous", "unsupported_version"}:
        reason = {
            "ambiguous": "ambiguous_runtime_managed_block",
            "unsupported_version": "unsupported_runtime_integration_version",
        }[inspection["state"]]
        return _blocked(project, reason, door="runtime", bridge=inspection)
    if remove:
        if inspection["state"] == "absent":
            return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
        desired = existing[: inspection["start"]] + existing[inspection["end"]:]
        return "CHANGE", deepcopy(project), {
            **base, "reason": None, "desired_bytes": desired,
            "integration_version": RUNTIME_CARD_VERSION,
        }
    # absent or stale → write expected_bytes (replace segment); installed → no-op
    if inspection["state"] == "installed":
        return "NO_CHANGE", deepcopy(project), {
            **base, "reason": None, "freshness": "current",
        }
    if inspection["state"] == "stale":
        stripped = existing[: inspection["start"]] + existing[inspection["end"]:]
        desired = place_managed_segment(
            stripped if stripped else None, expected_bytes,
        )
    else:
        desired = place_managed_segment(existing, expected_bytes)
    return "CHANGE", deepcopy(project), {
        **base, "reason": None, "desired_bytes": desired,
        "integration_version": RUNTIME_CARD_VERSION,
        "freshness": "stale" if inspection["state"] == "stale" else None,
    }


def plan_learning_integration(
    project: dict[str, Any],
    existing: bytes | None,
    *,
    expected_bytes: bytes,
    remove: bool = False,
    status_only: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Plan the knowledge-dependent learning card door (stale ≠ modified)."""
    workspace_reason = validate_claude_integration_workspace(project)
    inspection = inspect_learning_door(existing, expected_bytes)
    base = {
        "scope": "project",
        "door": "learning",
        "bridge_path": LEARNING_PATH,
        "bridge": inspection,
        "integration_version": inspection.get("version"),
        "doctrine_files": len(CORE_DOCTRINE_PATHS) if workspace_reason is None else 0,
        "enforcement": "guidance_only",
        "desired_bytes": existing,
        "workspace_reason": workspace_reason,
        "freshness": inspection.get("freshness"),
    }
    if status_only:
        state = inspection["state"]
        if workspace_reason is not None:
            return "BLOCKED", deepcopy(project), {
                **base,
                "reason": workspace_reason,
                "bridge_healthy": False,
                "workspace_compatible": False,
            }
        if state in {"ambiguous", "unsupported_version"}:
            reason = {
                "ambiguous": "ambiguous_learning_managed_block",
                "unsupported_version": "unsupported_learning_integration_version",
            }[state]
            return "BLOCKED", deepcopy(project), {
                **base,
                "reason": reason,
                "bridge_healthy": False,
                "workspace_compatible": True,
            }
        if state == "stale":
            return "NO_CHANGE", deepcopy(project), {
                **base,
                "reason": "learning_card_stale",
                "bridge_healthy": False,
                "workspace_compatible": True,
                "freshness": "stale",
            }
        if state == "absent":
            return "NO_CHANGE", deepcopy(project), {
                **base,
                "reason": "learning_integration_not_installed",
                "bridge_healthy": False,
                "workspace_compatible": True,
            }
        return "NO_CHANGE", deepcopy(project), {
            **base,
            "reason": None,
            "bridge_healthy": True,
            "workspace_compatible": True,
            "freshness": "current",
        }
    if workspace_reason is not None:
        return _blocked(project, workspace_reason, door="learning", bridge=inspection)
    guard = mutation_guard_metadata(project)
    if guard is not None:
        return _blocked(project, guard["reason"], door="learning", bridge=inspection)
    if inspection["state"] in {"ambiguous", "unsupported_version"}:
        reason = {
            "ambiguous": "ambiguous_learning_managed_block",
            "unsupported_version": "unsupported_learning_integration_version",
        }[inspection["state"]]
        return _blocked(project, reason, door="learning", bridge=inspection)
    if remove:
        if inspection["state"] == "absent":
            return "NO_CHANGE", deepcopy(project), {**base, "reason": None}
        desired = existing[: inspection["start"]] + existing[inspection["end"]:]
        return "CHANGE", deepcopy(project), {
            **base, "reason": None, "desired_bytes": desired,
            "integration_version": LEARNING_CARD_VERSION,
        }
    if inspection["state"] == "installed":
        return "NO_CHANGE", deepcopy(project), {
            **base, "reason": None, "freshness": "current",
        }
    if inspection["state"] == "stale":
        stripped = existing[: inspection["start"]] + existing[inspection["end"]:]
        desired = place_managed_segment(
            stripped if stripped else None, expected_bytes,
        )
    else:
        desired = place_managed_segment(existing, expected_bytes)
    return "CHANGE", deepcopy(project), {
        **base, "reason": None, "desired_bytes": desired,
        "integration_version": LEARNING_CARD_VERSION,
        "freshness": "stale" if inspection["state"] == "stale" else None,
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
        return ["agents", "claude", "gemini", "runtime", "learning"]
    if name in {"agents", "claude", "gemini", "runtime", "learning"}:
        return [name]
    raise ValueError(f"unsupported integration: {name}")
