import ast
import json
from pathlib import Path

import pytest

from aef import cli
from aef.filesystem import apply_workspace, load_workspace
from aef.operations import init_project
from aef.workspace_resolution import (
    CLI_WORKSPACE_CONSUMER_FUNCTIONS,
    CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS,
    apply_workspace_resolution_to_args,
    resolve_cli_workspace,
)
from tests.support.workspace_resolution_audit import (
    audit_cli_workspace_resolution_registries,
    find_workspace_resolution_bypasses,
    list_functions_referencing_args_workspace,
)

CLI_PATH = Path(__file__).resolve().parents[1] / "src" / "aef" / "cli.py"
CLI_SOURCE = CLI_PATH.read_text(encoding="utf-8")


def invoke_json(capsys, *arguments):
    code = cli.main(["--json", *arguments])
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured


def init_workspace(workspace: Path) -> None:
    current = load_workspace(workspace)
    status, desired, _ = init_project(
        current,
        instance_id="agent-ws-1",
        answers={"decision.role.primary.v1": "generalist-agent"},
        created_at="2026-08-25T10:00:00Z",
        profile="aef-v1",
    )
    assert status == "CHANGE"
    apply_workspace(workspace, current, desired)


def test_cli_workspace_resolution_sites_use_helper():
    registry_gaps = audit_cli_workspace_resolution_registries(CLI_SOURCE)
    assert registry_gaps == []

    referencing = list_functions_referencing_args_workspace(CLI_SOURCE)
    assert referencing == CLI_WORKSPACE_CONSUMER_FUNCTIONS | CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS

    bypasses = find_workspace_resolution_bypasses(CLI_SOURCE)
    assert bypasses == []

    parse_source = ast.get_source_segment(
        CLI_SOURCE,
        next(
            node
            for node in ast.walk(ast.parse(CLI_SOURCE))
            if isinstance(node, ast.FunctionDef) and node.name == "_parse_args"
        ),
    )
    assert parse_source is not None
    assert "apply_workspace_resolution_to_args" in parse_source


def test_workspace_resolution_bypass_guard_detects_circumvention():
    fake_source = """
def _run_bypass(args):
    workspace = args.workspace
    return Path(workspace).resolve()

def _run_getattr_bypass(args):
  ws = getattr(args, "workspace")
  return os.path.abspath(ws)
"""
    bypasses = find_workspace_resolution_bypasses(fake_source)
    assert len(bypasses) >= 2
    assert any("_run_bypass" in item for item in bypasses)
    assert any("_run_getattr_bypass" in item for item in bypasses)


def test_workspace_resolution_registry_gap_is_reported():
    fake_source = """
def _uses_workspace_without_registry(args):
    return args.workspace
"""
    gaps = audit_cli_workspace_resolution_registries(fake_source)
    assert gaps == [
        "_uses_workspace_without_registry references args.workspace but is missing from "
        "CLI_WORKSPACE_CONSUMER_FUNCTIONS and "
        "CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS"
    ]


def test_resolve_cli_workspace_walks_up_to_parent_agent(tmp_path):
    workspace = tmp_path / "project"
    subdir = workspace / "_upgrade"
    subdir.mkdir(parents=True)
    (workspace / ".agent").mkdir()
    (workspace / ".agent" / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(subdir)
        resolution = resolve_cli_workspace(None)

    assert resolution.workspace == workspace.resolve()
    assert resolution.walked_up is True
    assert resolution.workspace_source == "cwd"
    assert resolution.no_agent_in_start_or_ancestors is False


def test_resolve_cli_workspace_explicit_dot_does_not_walk(tmp_path):
    workspace = tmp_path / "project"
    subdir = workspace / "_upgrade"
    subdir.mkdir(parents=True)
    (workspace / ".agent").mkdir()
    (workspace / ".agent" / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(subdir)
        resolution = resolve_cli_workspace(".")

    assert resolution.workspace == subdir.resolve()
    assert resolution.walked_up is False
    assert resolution.workspace_source == "explicit"
    assert resolution.explicit_agent_workspace == workspace.resolve()


def test_audit_from_subdirectory_walks_up(tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    init_workspace(workspace)
    subdir = workspace / "_upgrade"
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    code, envelope, _ = invoke_json(capsys, "audit")

    assert code == 0
    assert envelope["status"] == "PASS"
    assert envelope["workspace"] == workspace.resolve().as_posix()
    assert envelope["meta"]["workspace_source"] == "cwd"
    assert envelope["meta"]["workspace_resolution"]["walked_up"] is True


def test_audit_without_agent_reports_context(tmp_path, capsys, monkeypatch):
    root = tmp_path / "bare"
    subdir = root / "sub"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    json_code, envelope, _ = invoke_json(capsys, "audit")
    assert json_code == 1
    assert envelope["status"] == "FAIL"
    assert envelope["meta"]["workspace_source"] == "cwd"
    assert envelope["meta"]["no_agent_in_start_or_ancestors"] is True
    assert envelope["workspace"] == subdir.resolve().as_posix()

    human_code = cli.main(["--human", "audit"])
    captured = capsys.readouterr()
    assert human_code == 1
    assert "no .agent/ here or in any parent directory" in captured.out


def test_explicit_workspace_dot_keeps_subdirectory_and_signals_parent(tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    init_workspace(workspace)
    subdir = workspace / "_upgrade"
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    code, envelope, captured = invoke_json(capsys, "--workspace", ".", "audit")

    assert code == 1
    assert envelope["workspace"] == subdir.resolve().as_posix()
    assert envelope["meta"]["workspace_source"] == "explicit"
    assert envelope["meta"]["explicit_workspace_notice"] == {
        "agent_workspace": workspace.resolve().as_posix(),
    }
    assert "workspace_resolution" not in envelope["meta"]
    assert "no .agent/ here or in any parent directory" not in captured.out

    human_code = cli.main(["--human", "--workspace", ".", "audit"])
    human_captured = capsys.readouterr()
    assert human_code == 1
    assert "explicit workspace has no .agent/" in human_captured.out
    assert workspace.resolve().as_posix() in human_captured.out


def test_apply_workspace_resolution_to_args_sets_path(tmp_path):
    import argparse

    args = argparse.Namespace(workspace=None)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(tmp_path)
        apply_workspace_resolution_to_args(args)

    assert isinstance(args.workspace, Path)
    assert args.workspace == tmp_path.resolve()
    assert args.workspace_resolution.workspace_source == "cwd"
