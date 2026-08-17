from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from aef.init_profiles import UnknownInitProfileError, get_init_profile
from aef.operations import DEFAULT_CORE_FILES, init_project


ROLE = "decision.role.primary.v1"
ROOT = Path(__file__).resolve().parents[1]
MISSING = object()


def _existing_v1_project(*, framework_version="1.0.0", schema_version="1.0.0",
                         framework="aef", instance_id="agent-1", role="generalist-agent"):
    manifest = {
        "framework": framework,
        "instance_id": instance_id,
        "created_at": "2026-08-13T18:00:00+02:00",
    }
    if framework_version is not MISSING:
        manifest["framework_version"] = framework_version
    if schema_version is not MISSING:
        manifest["schema_version"] = schema_version
    return {
        "files": {".agent/manifest.json": manifest},
        "decisions": {"decisions": [{
            "id": ROLE,
            "status": "resolved",
            "value": role,
            "source": "human-confirmed",
        }]},
    }


def test_official_v1_profile_is_defensive_and_canonical():
    profile = get_init_profile("aef-v1")
    pristine = get_init_profile("aef-v1")

    assert profile["framework"] == "aef"
    assert profile["framework_version"] == "1.0.0"
    assert profile["schema_version"] == "1.0.0"
    assert profile["required_decisions"] == [{
        "id": ROLE,
        "required": True,
        "value_type": "string",
        "allow_empty": False,
    }]
    assert profile["initial_files"][".agent/state/competencies.json"] == {}
    assert profile["initial_files"][".agent/state/evaluations.json"]["history"] == []
    assert profile["initial_files"][".agent/state/evaluations.json"]["schema_version"] == "1.0.0"
    assert profile["initial_files"][".agent/state/evaluations.json"]["promotion_recommendations"] == []
    assert profile["initial_files"][".agent/integrations/registry.json"] == {"connectors": []}
    assert profile["initial_files"][".agent/knowledge/knowledge.json"] == {
        "signals": [],
        "observations": [],
        "hypotheses": [],
        "rules": [],
        "principles": [],
        "mistakes": [],
    }

    profile["core_files"].clear()
    profile["initial_files"][".agent/state/migrations.json"]["applied"].append("mutated")

    assert get_init_profile("aef-v1") == pristine


def test_public_core_defaults_cannot_mutate_official_profile_or_legacy_init():
    public_before = deepcopy(DEFAULT_CORE_FILES)
    profile_before = get_init_profile("aef-v1")
    try:
        DEFAULT_CORE_FILES.clear()

        profile_after = get_init_profile("aef-v1")
        status, legacy = init_project({"files": {}}, instance_id="legacy-agent")[:2]

        assert profile_after == profile_before
        assert status == "CHANGE"
        for path, content in profile_before["core_files"].items():
            assert legacy["files"][path] == content
    finally:
        DEFAULT_CORE_FILES.clear()
        DEFAULT_CORE_FILES.update(public_before)


def test_unknown_init_profile_has_stable_explicit_error():
    with pytest.raises(UnknownInitProfileError, match=r"^unknown init profile: missing-profile$"):
        get_init_profile("missing-profile")


def test_official_v1_profile_blocks_without_required_role_without_mutation():
    source = {"files": {".agent/local/notes.txt": "preserve"}}
    before = deepcopy(source)

    status, out, meta = init_project(source, instance_id="agent-1", profile="aef-v1")

    assert status == "BLOCKED"
    assert out == source == before
    assert meta["unresolved_decisions"] == [ROLE]


def test_official_v1_profile_initializes_canonical_state_and_replays():
    source = {"files": {".agent/local/notes.txt": "preserve"}}
    answers = {ROLE: "generalist-agent"}
    inputs_before = deepcopy((source, answers))

    status, initialized, meta = init_project(
        source,
        instance_id="agent-1",
        profile="aef-v1",
        answers=answers,
        created_at="2026-08-13T18:00:00+02:00",
    )

    assert status == "CHANGE"
    assert meta["unresolved_decisions"] == []
    assert initialized["files"][".agent/manifest.json"] == {
        "framework": "aef",
        "framework_version": "1.0.0",
        "schema_version": "1.0.0",
        "instance_id": "agent-1",
        "created_at": "2026-08-13T18:00:00+02:00",
    }
    assert initialized["files"][".agent/state/migrations.json"] == {"applied": []}
    assert initialized["files"][".agent/state/evaluations.json"] == {
        "schema_version": "1.0.0",
        "policy": {
            "mode": "adaptive", "every_tasks": None, "interval_days": None,
        },
        "history": [],
        "promotion_recommendations": [],
    }
    assert initialized["files"][".agent/local/notes.txt"] == "preserve"
    assert (source, answers) == inputs_before

    replay_status, replayed, replay_meta = init_project(
        initialized,
        instance_id="agent-1",
        profile="aef-v1",
        answers=answers,
        created_at="2026-08-13T18:00:00+02:00",
    )

    assert replay_status == "NO_CHANGE"
    assert replayed == initialized
    assert replay_meta["unresolved_decisions"] == []


@pytest.mark.parametrize("invalid_role", [None, 42, "", "   "])
def test_official_v1_profile_rejects_invalid_role_without_mutation(invalid_role):
    source = {"files": {}}

    status, out, meta = init_project(
        source,
        instance_id="agent-1",
        profile="aef-v1",
        answers={ROLE: invalid_role},
    )

    assert status == "BLOCKED"
    assert out == source
    assert meta["reason"] == "invalid_decision"


def test_init_blocks_incompatible_framework_without_mutation():
    source = {
        "files": {
            ".agent/manifest.json": {
                "framework": "other-framework",
                "instance_id": "agent-1",
            }
        }
    }
    before = deepcopy(source)

    status, out, meta = init_project(source, instance_id="agent-1")

    assert status == "BLOCKED"
    assert out == source == before
    assert meta["reason"] == "framework_mismatch"


def test_init_blocks_instance_identity_conflict_without_mutation():
    source = {
        "files": {
            ".agent/manifest.json": {
                "framework": "aef",
                "instance_id": "existing-agent",
            }
        }
    }
    before = deepcopy(source)

    status, out, meta = init_project(source, instance_id="requested-agent")

    assert status == "BLOCKED"
    assert out == source == before
    assert meta == {
        "reason": "instance_id_mismatch",
        "expected_instance_id": "existing-agent",
        "requested_instance_id": "requested-agent",
        "unresolved_decisions": [],
    }


def test_init_blocks_conflicting_resolved_decision_without_mutation():
    source = {
        "files": {},
        "decisions": {
            "decisions": [{
                "id": ROLE,
                "status": "resolved",
                "value": "support-specialist",
                "source": "human-confirmed",
            }]
        },
    }
    before = deepcopy(source)

    status, out, meta = init_project(
        source,
        instance_id="agent-1",
        profile="aef-v1",
        answers={ROLE: "generalist-agent"},
    )

    assert status == "BLOCKED"
    assert out == source == before
    assert meta["reason"] == "decision_conflict"
    assert meta["decision_id"] == ROLE


@pytest.mark.parametrize("actual_version", ["0.9.0", "1.1.0", MISSING, 100])
def test_explicit_profile_blocks_framework_version_mismatch(actual_version):
    source = _existing_v1_project(framework_version=actual_version)
    before = deepcopy(source)

    status, out, meta = init_project(source, instance_id="agent-1", profile="aef-v1")

    assert status == "BLOCKED"
    assert out == source == before
    assert meta == {
        "reason": "framework_version_mismatch",
        "expected_version": "1.0.0",
        "actual_version": None if actual_version is MISSING else actual_version,
        "message": "Use UPGRADE to align the workspace framework version before INIT.",
        "unresolved_decisions": [],
    }


@pytest.mark.parametrize("actual_version", ["0.9.0", "1.1.0", MISSING, 100])
def test_explicit_profile_blocks_schema_version_mismatch(actual_version):
    source = _existing_v1_project(schema_version=actual_version)
    before = deepcopy(source)

    status, out, meta = init_project(source, instance_id="agent-1", profile="aef-v1")

    assert status == "BLOCKED"
    assert out == source == before
    assert meta == {
        "reason": "schema_version_mismatch",
        "expected_version": "1.0.0",
        "actual_version": None if actual_version is MISSING else actual_version,
        "message": "Use UPGRADE to align the workspace schema version before INIT.",
        "unresolved_decisions": [],
    }


def test_explicit_profile_version_preflight_order_is_deterministic():
    cases = [
        (
            _existing_v1_project(
                framework="other", instance_id="other-agent",
                framework_version="0.9.0", schema_version="0.9.0",
            ),
            "framework_mismatch",
        ),
        (
            _existing_v1_project(
                instance_id="other-agent", framework_version="0.9.0", schema_version="0.9.0",
            ),
            "instance_id_mismatch",
        ),
        (
            _existing_v1_project(framework_version="0.9.0", schema_version="0.9.0"),
            "framework_version_mismatch",
        ),
        (
            _existing_v1_project(schema_version="0.9.0", role="support-specialist"),
            "schema_version_mismatch",
        ),
    ]

    for source, expected_reason in cases:
        status, out, meta = init_project(
            source,
            instance_id="agent-1",
            profile="aef-v1",
            answers={ROLE: "generalist-agent"},
        )
        assert status == "BLOCKED"
        assert out == source
        assert meta["reason"] == expected_reason


def test_matching_profile_versions_allow_normal_replay():
    source = _existing_v1_project()

    status, out, meta = init_project(source, instance_id="agent-1", profile="aef-v1")

    assert status == "CHANGE"
    assert out["files"][".agent/manifest.json"] == source["files"][".agent/manifest.json"]
    assert meta["unresolved_decisions"] == []


def test_legacy_init_does_not_enforce_explicit_profile_versions():
    source = _existing_v1_project(framework_version="0.1.0", schema_version="0.8.0")

    status, out, _ = init_project(source, instance_id="agent-1")

    assert status == "CHANGE"
    assert out["files"][".agent/manifest.json"]["framework_version"] == "0.1.0"
    assert out["files"][".agent/manifest.json"]["schema_version"] == "0.8.0"


def test_successful_v1_init_produces_documents_matching_existing_schemas():
    status, initialized, _ = init_project(
        {"files": {}},
        instance_id="agent-1",
        profile="aef-v1",
        answers={ROLE: "generalist-agent"},
        created_at="2026-08-13T18:00:00+02:00",
    )
    schema_by_path = {
        ".agent/manifest.json": "manifest.schema.json",
        ".agent/state/career.json": "career.schema.json",
        ".agent/state/competencies.json": "competencies.schema.json",
        ".agent/state/evaluations.json": "evaluation.schema.json",
        ".agent/integrations/registry.json": "capability.schema.json",
        ".agent/knowledge/knowledge.json": "knowledge.schema.json",
        ".agent/state/migrations.json": "migrations.schema.json",
    }

    assert status == "CHANGE"
    for path, schema_name in schema_by_path.items():
        schema = json.loads((ROOT / "src/aef/schemas" / schema_name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(initialized["files"][path])
