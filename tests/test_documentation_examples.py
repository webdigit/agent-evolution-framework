from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

import aef.cli as cli
from aef.consolidation import validate_consolidation_document
from aef.evaluation_engine import validate_evaluation_decisions
from aef.filesystem import apply_workspace, load_workspace
from aef.knowledge_state import validate_knowledge_state
from aef.operations import validate_discovery_snapshot
from aef.promotion_recommendations import validate_evaluation_state
from aef.ingest_intake import validate_ingest_submission
from aef.competency_declaration import validate_competency_declaration
from aef.record_document import build_persisted_record, validate_record_submission
from aef.schema_validation import draft202012_validator, load_packaged_schema
from aef.strict_json import validate_strict_json


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"
DIGEST = "sha256:0040087530564ecf50925019a020cfe486ccf3c4c49d13fdd6d311432b443d92"
AUTO_MEMORY_PATH = "`~/.claude/projects/<project>/memory/`"
_EXPLICIT_AUTO_MEMORY_LOCATION = re.compile(
    r"By default,?\s+Claude Code stores Auto Memory under\s+"
    + rf"(?-i:{re.escape(AUTO_MEMORY_PATH)})",
    re.IGNORECASE,
)
_PRONOUN_AUTO_MEMORY_LOCATION = re.compile(
    r"By default,?\s+Claude Code stores it under\s+"
    + rf"(?-i:{re.escape(AUTO_MEMORY_PATH)})",
    re.IGNORECASE,
)


def _has_qualified_auto_memory_location(text: str) -> bool:
    paragraphs = [
        " ".join(paragraph.split())
        for paragraph in re.split(r"(?:\r?\n){2,}", text)
        if paragraph.strip()
    ]
    for paragraph in paragraphs:
        if _EXPLICIT_AUTO_MEMORY_LOCATION.search(paragraph):
            return True
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        for index, sentence in enumerate(sentences):
            if not _PRONOUN_AUTO_MEMORY_LOCATION.search(sentence):
                continue
            if index > 0 and re.search(
                r"\bAuto Memory\b",
                sentences[index - 1],
                re.IGNORECASE,
            ):
                return True
    return False


def _assert_auto_memory_location_is_qualified(text: str) -> None:
    normalized = " ".join(text.split())
    assert _has_qualified_auto_memory_location(text)
    assert re.search(
        r"(?:Claude Code can configure a different location through|"
        r"A different location can be configured with)\s+"
        r"(?-i:`autoMemoryDirectory`)",
        normalized,
        re.IGNORECASE,
    )
    assert re.search(
        r"AEF does not read those settings to resolve\s+"
        r"(?-i:`autoMemoryDirectory`)(?![A-Za-z0-9_])",
        normalized,
        re.IGNORECASE,
    )


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
    recording = validate_record_submission(_document("recording.json"))
    persisted = build_persisted_record(recording)
    assert persisted["protocol"] == "aef.record/v1"
    assert persisted["digest"].startswith("sha256:")


def test_documentation_examples_execute_real_cli_dry_runs(tmp_path, capsys):
    workspace = tmp_path / "Example Workspace 日本"
    source = _project()
    apply_workspace(workspace, load_workspace(workspace), source)
    before = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}

    commands = [
        ["discover", "--snapshot", str(EXAMPLES / "connectors.json")],
        ["consolidate", "--reviews", str(EXAMPLES / "reviews.json")],
        ["evaluate", "--decisions", str(EXAMPLES / "evaluation-decisions.json")],
        ["record", "--recording", str(EXAMPLES / "recording.json")],
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
    installation = (ROOT / "docs/installation.md").read_text(encoding="utf-8")
    getting_started = (ROOT / "docs/getting-started.md").read_text(encoding="utf-8")
    commands = (ROOT / "docs/commands.md").read_text(encoding="utf-8")
    troubleshooting = (ROOT / "docs/troubleshooting.md").read_text(encoding="utf-8")
    claude = (ROOT / "docs/claude-integration.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/input-files.md").read_text(encoding="utf-8")

    assert (ROOT / "docs/input-files.md").is_file()
    for name in ("connectors.json", "reviews.json", "evaluation-decisions.json", "recording.json"):
        assert (EXAMPLES / name).is_file()
        assert f"examples/{name}" in guide
    for fragment in (
        "evaluate --refresh --dry-run",
        "evaluate --recover --dry-run",
        "record --recording FILE [--dry-run]",
        "ingest --intake FILE [--dry-run]",
        "competency declare --declaration FILE [--dry-run]",
    ):
        assert fragment in commands or fragment in guide
    assert "Canonical input files" in readme
    assert "pip install agent-evolution-framework" not in readme
    assert "--list` is strictly read-only" in commands
    assert "--refresh` can modify" in commands
    assert "Personal User Name" not in guide

    current_wheel_url = (
        "https://github.com/webdigit/agent-evolution-framework/releases/download/v2.0.0/"
        "agent_evolution_framework-2.0.0-py3-none-any.whl"
    )
    for document in (readme, installation, getting_started):
        assert current_wheel_url in document
    for document in (readme, installation, getting_started):
        assert "does not require Git" in document
        assert "air-gap" in document
        assert "SHA256SUMS.txt" in document
        assert "pip install agent-evolution-framework" not in document

    for document in (readme, getting_started, commands, troubleshooting):
        assert "--instance-id" in document
        assert "--created-at" in document
        assert "same values" in document
    assert "dry_run_requires_stable_inputs" in troubleshooting

    assert ".claude/CLAUDE.md" in claude
    assert "@../.agent/core/" in claude
    assert "sibling" in claude
    memory_section = claude.split("## Claude Code memory boundaries", 1)[1]
    _assert_auto_memory_location_is_qualified(memory_section)
    assert "https://code.claude.com/docs/en/memory" in memory_section
    assert "does not inspect, modify, or normalize `~/.claude`" in claude
    assert "Auto Memory does not write to `.claude/CLAUDE.md`" in claude
    assert "Auto Memory writes to `.claude/CLAUDE.md`" not in claude


@pytest.mark.parametrize("text", [
    (
        "Auto Memory is separate. Claude Code stores Auto Memory under "
        f"{AUTO_MEMORY_PATH}. Claude Code can configure a different location through "
        "`autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`."
    ),
    (
        "By default, AEF uses project scope. Claude Code keeps Auto Memory under "
        f"{AUTO_MEMORY_PATH}. Claude Code can configure a different location through "
        "`autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "A different location can be configured. AEF does not read those settings "
        "to resolve `autoMemoryDirectory`."
    ),
    (
        "By default, AEF uses project scope. Claude Code stores Auto Memory under "
        "an unspecified location. Claude Code can configure a different location "
        "through `autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through another setting. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure this behavior through `autoMemoryDirectory`. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores it under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `autoMemoryDirectory`. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores it under {AUTO_MEMORY_PATH}. Auto Memory "
        "is a separate mechanism. Claude Code can configure a different location "
        "through `autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`."
    ),
    (
        "Auto Memory is discussed for another feature. The project settings control "
        f"this behavior. By default, Claude Code stores it under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `autoMemoryDirectory`. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        "Auto Memory is discussed in this independent paragraph.\n\n"
        f"By default, Claude Code stores it under {AUTO_MEMORY_PATH}. Claude Code can "
        "configure a different location through `autoMemoryDirectory`. AEF does not "
        "read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        "Auto Memory is documented separately. The settings file is the immediate "
        f"subject. By default, Claude Code stores it under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `autoMemoryDirectory`. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        f"Always, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. Claude Code "
        "can configure a different location through `autoMemoryDirectory`. AEF does "
        "not read those settings to resolve `autoMemoryDirectory`."
    ),
    (
        "By default, Claude Code stores Auto Memory under "
        "`~/.claude/projects/<PROJECT>/memory/`. Claude Code can configure a "
        "different location through `autoMemoryDirectory`. AEF does not read those "
        "settings to resolve `autoMemoryDirectory`."
    ),
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `automemorydirectory`. "
        "AEF does not read those settings to resolve `automemorydirectory`."
    ),
])
def test_auto_memory_location_contract_rejects_unqualified_variants(text):
    with pytest.raises(AssertionError):
        _assert_auto_memory_location_is_qualified(text)


@pytest.mark.parametrize("text", [
    (
        f"By default, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `autoMemoryDirectory` "
        "in supported user or policy settings. AEF does not read those settings to "
        "resolve `autoMemoryDirectory`."
    ),
    (
        "Auto Memory is distinct from CLAUDE.md. By default, Claude Code stores it "
        f"under {AUTO_MEMORY_PATH}. A different location can be configured with "
        "`autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`."
    ),
    (
        f"By default Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through `autoMemoryDirectory`. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`"
    ),
    (
        f"BY DEFAULT, Claude Code stores Auto Memory under {AUTO_MEMORY_PATH}. "
        "A different location can be configured with `autoMemoryDirectory`. "
        "AEF does not read those settings to resolve `autoMemoryDirectory`"
    ),
    (
        "Claude Code's `CLAUDE.md` instructions and Auto Memory are separate "
        "mechanisms. By default Claude Code stores it under "
        f"{AUTO_MEMORY_PATH}. Claude Code can configure a different location through "
        "`autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`"
    ),
])
def test_auto_memory_location_contract_accepts_qualified_variants(text):
    _assert_auto_memory_location_is_qualified(text)


def test_auto_memory_pronoun_without_antecedent_is_rejected():
    audit_reproduction = (
        f"By default, Claude Code stores it under {AUTO_MEMORY_PATH}. "
        "Claude Code can configure a different location through "
        "`autoMemoryDirectory`. AEF does not read those settings to resolve "
        "`autoMemoryDirectory`."
    )

    with pytest.raises(AssertionError):
        _assert_auto_memory_location_is_qualified(audit_reproduction)

def test_ingest_example_document_validates_against_intake_contract():
    intake = validate_ingest_submission(_document("ingest.json"))
    assert intake["protocol"] == "aef.ingest.submit/v1"
    assert intake["records"]

def test_competency_declaration_example_validates_against_declare_contract():
    declaration = validate_competency_declaration(_document("competency-declaration.json"))
    assert declaration["protocol"] == "aef.competency.declare.submit/v1"
    assert declaration["competency_id"]
