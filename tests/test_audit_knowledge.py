from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

from conftest import installed_aef_script

from aef import cli as aef_cli
from aef.filesystem import apply_workspace, load_workspace
from aef.init_profiles import get_init_profile
from aef.operations import audit_project, init_project
from aef.schema_validation import draft202012_validator, validate_persisted_knowledge


KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"
ROOT = Path(__file__).resolve().parents[1]


def _human_workspace_line(workspace: Path) -> str:
    return f"Workspace : {aef_cli._escape_human_value(str(workspace.resolve()))}\n"


def _initialized_project():
    status, project, _ = init_project(
        {"files": {}, "decisions": {"decisions": []}},
        instance_id="audit-agent", profile="aef-v1",
        answers={"decision.role.primary.v1": "generalist-agent"},
        created_at="2026-08-14T10:00:00Z",
    )
    assert status == "CHANGE"
    return project


def _invalid_root_extension():
    state = deepcopy(get_init_profile("aef-v1")["initial_files"][KNOWLEDGE_PATH])
    state["unicode_extension"] = {"label": "日本"}
    return state


def _invalid_lifecycle(timestamp="2026-08-14T14:00:00Z", action="retire"):
    state = deepcopy(get_init_profile("aef-v1")["initial_files"][KNOWLEDGE_PATH])
    state["rules"] = [{
        "id": "rule:audit", "type": "rule", "status": "retired",
        "lifecycle": {"retired": {
            "review_id": "review:audit", "rule_id": "rule:audit", "action": action,
            "reason": "Audited retirement.", "evidence_ids": [],
            "approval": {
                "approved": True, "source": "human", "actor": "Alex Example",
                "approved_at": timestamp,
            },
        }},
    }]
    return state


def test_audit_accepts_official_init_knowledge_without_mutation():
    project = _initialized_project()
    before = deepcopy(project)

    result = audit_project(project)

    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert project == before


def test_audit_reports_missing_knowledge_without_materializing_it():
    project = _initialized_project()
    project["files"].pop(KNOWLEDGE_PATH)
    before = deepcopy(project)

    result = audit_project(project)

    assert result["status"] == "FAIL"
    assert result["findings"] == [
        {"id": "missing-knowledge-state", "severity": "error"}
    ]
    assert project == before
    assert KNOWLEDGE_PATH not in project["files"]


def test_audit_findings_have_deterministic_order_when_required_files_are_missing():
    without_manifest = _initialized_project()
    without_manifest["files"].pop(".agent/manifest.json")
    without_manifest["files"].pop(KNOWLEDGE_PATH)
    assert audit_project(without_manifest)["findings"] == [
        {"id": "missing-manifest", "severity": "error"},
        {"id": "missing-knowledge-state", "severity": "error"},
    ]

    without_ledger = _initialized_project()
    without_ledger["files"].pop(".agent/state/migrations.json")
    without_ledger["files"].pop(KNOWLEDGE_PATH)
    assert audit_project(without_ledger)["findings"] == [
        {"id": "missing-migration-ledger", "severity": "warning"},
        {"id": "missing-knowledge-state", "severity": "error"},
    ]


@pytest.mark.parametrize("invalid", [
    _invalid_root_extension(),
    {"observations": [], "rules": [], "principles": []},
    _invalid_lifecycle(action="specialize"),
    _invalid_lifecycle("2026-02-30T14:00:00Z"),
])
def test_audit_reports_structurally_invalid_knowledge_without_mutation(invalid):
    project = _initialized_project()
    project["files"][KNOWLEDGE_PATH] = invalid
    before = deepcopy(project)

    result = audit_project(project)

    assert result["status"] == "FAIL"
    assert result["findings"] == [
        {"id": "invalid-knowledge-state", "severity": "error"}
    ]
    assert not any(item["id"] == "missing-knowledge-state" for item in result["findings"])
    assert project == before


def test_audit_catches_business_rejection_even_when_schema_accepts_document():
    project = _initialized_project()
    state = deepcopy(project["files"][KNOWLEDGE_PATH])
    duplicate = {"id": "observation:duplicate", "type": "observation", "status": "active"}
    state["observations"] = [duplicate, deepcopy(duplicate)]
    schema = json.loads((ROOT / "src/aef/schemas/knowledge.schema.json").read_text(encoding="utf-8"))
    draft202012_validator(schema).validate(state)
    project["files"][KNOWLEDGE_PATH] = state

    result = audit_project(project)

    assert result["status"] == "FAIL"
    assert result["findings"] == [{"id": "invalid-knowledge-state", "severity": "error"}]


def test_official_schema_path_rejects_schema_invalid_document():
    with pytest.raises(Exception):
        validate_persisted_knowledge(_invalid_root_extension())


@pytest.mark.parametrize("launcher", ["module", "script"])
@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_audit_invalid_knowledge_subprocess_modes_are_read_only(tmp_path, launcher, mode):
    workspace = tmp_path / f"audit knowledge 日本 {launcher} {mode}"
    project = _initialized_project()
    project["files"][KNOWLEDGE_PATH] = _invalid_root_extension()
    apply_workspace(workspace, load_workspace(workspace), project)
    knowledge = workspace / KNOWLEDGE_PATH
    before = knowledge.read_bytes()
    python = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(python), "-m", "aef"] if launcher == "module" else [str(script)]
    option = "--human" if mode == "human" else f"--{mode}"

    completed = subprocess.run(
        [*prefix, option, "--workspace", str(workspace), "audit"],
        input="", capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 1
    assert knowledge.read_bytes() == before
    assert "Traceback" not in completed.stdout + completed.stderr
    assert "unicode_extension" not in completed.stdout + completed.stderr
    if mode == "human":
        workspace_line = completed.stdout.splitlines()[-1]
        assert workspace_line.startswith("Workspace : ")
        assert completed.stdout == (
            "[FAILED] AEF audit found problems\n\n- invalid knowledge state\n"
            + workspace_line
            + "\n"
        )
    else:
        envelope = json.loads(completed.stdout)
        assert envelope["status"] == "FAIL"
        assert envelope["result"]["findings"] == [
            {"id": "invalid-knowledge-state", "severity": "error"}
        ]
        if mode == "compact":
            assert completed.stdout.count("\n") == 1


@pytest.mark.parametrize("launcher", ["module", "script"])
@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_audit_missing_knowledge_subprocess_is_read_only(tmp_path, launcher, mode):
    workspace = tmp_path / f"missing knowledge {launcher} {mode}"
    apply_workspace(workspace, load_workspace(workspace), _initialized_project())
    knowledge = workspace / KNOWLEDGE_PATH
    knowledge.unlink()
    before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*") if path.is_file()
    }
    python = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(python), "-m", "aef"] if launcher == "module" else [str(script)]
    option = "--human" if mode == "human" else f"--{mode}"

    completed = subprocess.run(
        [*prefix, option, "--workspace", str(workspace), "audit"],
        input="", capture_output=True, text=True, check=False,
    )
    after = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*") if path.is_file()
    }

    assert completed.returncode == 1
    assert before == after
    assert not knowledge.exists()
    assert not list(workspace.rglob("*.tmp"))
    assert "Traceback" not in completed.stdout + completed.stderr
    if mode == "human":
        assert completed.stdout == (
            "[FAILED] AEF audit found problems\n\n- missing knowledge state\n"
            + _human_workspace_line(workspace)
        )
    else:
        envelope = json.loads(completed.stdout)
        assert envelope["status"] == "FAIL"
        assert envelope["result"]["findings"] == [
            {"id": "missing-knowledge-state", "severity": "error"}
        ]
        if mode == "compact":
            assert completed.stdout.count("\n") == 1
