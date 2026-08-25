"""Resolve the CLI workspace root, including optional walk-up to a parent `.agent/`."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AGENT_DIR_NAME = ".agent"

# cli.py handlers that consume pre-resolved workspace from args.workspace.
CLI_WORKSPACE_CONSUMER_FUNCTIONS = frozenset(
    {
        "_run_init",
        "_run_audit",
        "_run_record",
        "_run_ingest",
        "_run_competency_declare",
        "_run_discover",
        "_run_consolidate",
        "_run_evaluate",
        "_run_integrate",
        "_run_doctor",
        "_run_upgrade",
        "_error_envelope",
    }
)

# cli.py functions that reference args.workspace without resolving it locally.
CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS = frozenset()


@dataclass(frozen=True)
class WorkspaceResolution:
    workspace: Path
    start: Path
    workspace_source: Literal["explicit", "cwd"]
    walked_up: bool
    no_agent_in_start_or_ancestors: bool
    explicit_agent_workspace: Path | None = None


def _has_agent_dir(path: Path) -> bool:
    return (path / AGENT_DIR_NAME).is_dir()


def _find_agent_in_parent_chain(start: Path) -> Path | None:
    current = start
    while True:
        parent = current.parent
        if parent == current:
            return None
        current = parent
        if _has_agent_dir(current):
            return current


def _agent_in_ancestor_chain(start: Path) -> bool:
    if _has_agent_dir(start):
        return True
    return _find_agent_in_parent_chain(start) is not None


def _walk_up_for_agent(start: Path) -> tuple[Path, bool, bool]:
    """Walk parents without re-resolving through symlinks outside the ancestor chain."""
    if _has_agent_dir(start):
        return start, False, False

    current = start
    while True:
        parent = current.parent
        if parent == current:
            break
        current = parent
        if _has_agent_dir(current):
            return current, True, False

    return start, False, True


def resolve_cli_workspace(workspace_arg: str | None) -> WorkspaceResolution:
    if workspace_arg is not None:
        start = Path(workspace_arg).resolve()
        workspace = start
        walked_up = False
        explicit_agent_workspace = None
        if _has_agent_dir(start):
            no_agent = False
        else:
            explicit_agent_workspace = _find_agent_in_parent_chain(start)
            no_agent = explicit_agent_workspace is None
        workspace_source: Literal["explicit", "cwd"] = "explicit"
    else:
        start = Path.cwd().resolve()
        workspace, walked_up, no_agent = _walk_up_for_agent(start)
        explicit_agent_workspace = None
        workspace_source = "cwd"

    return WorkspaceResolution(
        workspace=workspace,
        start=start,
        workspace_source=workspace_source,
        walked_up=walked_up,
        no_agent_in_start_or_ancestors=no_agent,
        explicit_agent_workspace=explicit_agent_workspace,
    )


def apply_workspace_resolution_to_args(args: argparse.Namespace) -> None:
    resolution = resolve_cli_workspace(args.workspace)
    args.workspace = resolution.workspace
    args.workspace_resolution = resolution


def workspace_resolution_meta(
    resolution: WorkspaceResolution | None,
) -> dict[str, Any]:
    if resolution is None:
        return {}
    meta: dict[str, Any] = {"workspace_source": resolution.workspace_source}
    if resolution.walked_up:
        meta["workspace_resolution"] = {
            "walked_up": True,
            "from": resolution.start.as_posix(),
        }
    if resolution.no_agent_in_start_or_ancestors:
        meta["no_agent_in_start_or_ancestors"] = True
    if resolution.explicit_agent_workspace is not None:
        meta["explicit_workspace_notice"] = {
            "agent_workspace": resolution.explicit_agent_workspace.as_posix(),
        }
    return meta
