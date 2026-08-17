from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import jsonschema

import aef.cli as cli
from aef.consolidation import validate_consolidation_document
from aef.evaluation_engine import validate_evaluation_decisions
from aef.filesystem import apply_workspace, load_workspace
from aef.knowledge_state import validate_knowledge_state
from aef.operations import validate_discovery_snapshot
from aef.promotion_recommendations import validate_evaluation_state
from aef.schema_validation import draft202012_validator, load_packaged_schema
from aef.strict_json import validate_strict_json


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"
DIGEST = "sha256:0040087530564ecf50925019a020cfe486ccf3c4c49d13fdd6d311432b443d92"


def _document(name: str):
    raw = (EXAMPLES / name).read_text(encoding="utf-8")
    document = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    validate_strict_json(document)
    json.dumps(document, sort_keys=True, allow_nan=False)
    return document


def _knowledge():
    return {
        "signals": [],
        "observations": [{
            "id": "observation:source-check", "type": "observation", "status": "active",
        }],
        "hypotheses": [],
        "rules": [{
            "id": "rule:source-check", "type": "rule", "status": "active",
            "pattern_key": "source-check", "evidence_ids": ["observation:source-check"],
        }],
        "principles": [],
        "mistakes": [],
    }


def _recommendation():
    return {
        "id": "promotion:career:global:L1:L2", "type": "promotion", "scope": "career",
        "competency_id": None, "from_level": "L1", "to_level": "L2", "status": "pending",
        "detected_at": "2026-08-14T10:00:00Z",
        "evidence": {"xp": 50, "cases": 10, "trust": 0.9, "complex_cases": 0,
                     "recent_significant_errors": 0},
        "evidence_digest": DIGEST,
    }


def _project():
    return {"files": {
        ".agent/manifest.json": {"framework": "aef", "framework_version": "1.0.0",
                                 "schema_version": "1.0.0", "instance_id": "example",
                                 "created_at": "2026-08-14T10:00:00Z"},
        ".agent/state/migrations.json": {"applied": []},
        ".agent/integrations/registry.json": {"connectors": []},
        ".agent/knowledge/knowledge.json": _knowledge(),
        ".agent/state/evaluations.json": {
            "schema_version": "1.0.0",
            "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
            "history": [], "promotion_recommendations": [_recommendation()],
        },
        ".agent/state/career.json": {
            "level": "L1", "xp": 50, "cases": 10, "trust": 0.9, "complex_cases": 0,
            "recent_significant_errors": 0, "status": "active", "probation": False,
        },
        ".agent/state/competencies.json": {},
    }}


def test_documentation_examples_are_strict_and_use_official_validators():
    connectors = _document("connectors.json")
    assert validate_discovery_snapshot(connectors)["connectors"]
    draft202012_validator(load_packaged_schema("capability.schema.json")).validate(
        connectors
    )
    assert validate_consolidation_document(_document("reviews.json"))["reviews"]
    assert validate_evaluation_decisions(_document("evaluation-decisions.json"))["decisions"]


def test_documentation_examples_execute_real_cli_dry_runs(tmp_path, capsys):
    workspace = tmp_path / "Example Workspace 日本"
    source = _project()
    apply_workspace(workspace, load_workspace(workspace), source)
    before = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}

    commands = [
        ["discover", "--snapshot", str(EXAMPLES / "connectors.json")],
        ["consolidate", "--reviews", str(EXAMPLES / "reviews.json")],
        ["evaluate", "--decisions", str(EXAMPLES / "evaluation-decisions.json")],
    ]
    for command in commands:
        code = cli.main(["--json", "--workspace", str(workspace), *command, "--dry-run"])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 0
        assert envelope["status"] == "CHANGE"
        assert envelope["dry_run"] is True

    after = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert after == before


def test_example_results_cross_validate_with_persisted_schemas():
    source = _project()
    validate_knowledge_state(source["files"][".agent/knowledge/knowledge.json"])
    validate_evaluation_state(source["files"][".agent/state/evaluations.json"])
    jsonschema.Draft202012Validator(load_packaged_schema("knowledge.schema.json"),
                                   format_checker=jsonschema.FormatChecker()).validate(
        source["files"][".agent/knowledge/knowledge.json"]
    )
    jsonschema.Draft202012Validator(load_packaged_schema("evaluation.schema.json"),
                                   format_checker=jsonschema.FormatChecker()).validate(
        source["files"][".agent/state/evaluations.json"]
    )


def test_documentation_links_and_command_claims_are_current():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = (ROOT / "docs/commands.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/input-files.md").read_text(encoding="utf-8")

    assert (ROOT / "docs/input-files.md").is_file()
    for name in ("connectors.json", "reviews.json", "evaluation-decisions.json"):
        assert (EXAMPLES / name).is_file()
        assert f"examples/{name}" in guide
    for fragment in ("evaluate --refresh --dry-run", "evaluate --recover --dry-run"):
        assert fragment in commands or fragment in guide
    assert "Canonical input files" in readme
    assert "pip install agent-evolution-framework" not in readme
    assert "--list` is strictly read-only" in commands
    assert "--refresh` can modify" in commands
    assert "Personal User Name" not in guide
