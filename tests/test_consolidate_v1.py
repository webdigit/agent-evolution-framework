from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import jsonschema

import aef.cli as cli
from aef.consolidation import (
    InvalidConsolidationInputError,
    validate_consolidation_document,
)
from aef.init_profiles import get_init_profile
from aef.filesystem import apply_workspace, load_workspace, render_workspace_plan
from aef.knowledge_state import InvalidKnowledgeStateError, validate_knowledge_state
from aef.operations import consolidate_knowledge, consolidate_project
from aef.schema_validation import draft202012_validator


KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"


def _rule():
    return {
        "id": "rule:verify-source",
        "type": "rule",
        "status": "active",
        "pattern_key": "verify-source",
        "evidence_ids": ["observation:one", "observation:two"],
    }


def _knowledge():
    return {
        "signals": [],
        "observations": [
            {"id": "observation:one", "type": "observation", "status": "active"},
            {"id": "observation:two", "type": "observation", "status": "active"},
        ],
        "hypotheses": [],
        "rules": [_rule()],
        "principles": [],
        "mistakes": [],
    }


def _approval():
    return {
        "approved": True,
        "source": "human",
        "actor": "Alex Example",
        "approved_at": "2026-08-14T14:00:00Z",
    }


def _specialize_review():
    return {
        "id": "review:specialize:verify-source",
        "rule_id": "rule:verify-source",
        "action": "specialize",
        "reason": "Only ambiguous records need extra verification.",
        "evidence_ids": ["observation:one", "observation:two"],
        "context": {"record_type": "ambiguous"},
        "approval": _approval(),
    }


def _project(knowledge=None):
    return {
        "files": {
            ".agent/manifest.json": {"framework": "aef"},
            KNOWLEDGE_PATH: deepcopy(knowledge or _knowledge()),
            ".agent/integrations/registry.json": {"connectors": []},
        }
    }


def _legacy_event(action):
    event = {
        "review_id": f"review:legacy:{action}", "action": action,
        "reason": "Legacy reviewed transition.",
        "evidence_ids": ["observation:one"], "approval": _approval(),
    }
    if action == "specialize":
        event["context"] = {"record_type": "ambiguous"}
    elif action == "supersede":
        event["replacement_id"] = "rule:verify-source:v2"
    return event


def _historical_knowledge(status, event_name):
    knowledge = _knowledge()
    rule = knowledge["rules"][0]
    rule["status"] = status
    rule["lifecycle"] = {
        event_name: {"reason": "Historical transition.", "evidence_ids": ["observation:one"]}
    }
    if status == "specialized":
        rule["context"] = {"record_type": "ambiguous"}
    elif status == "superseded":
        rule["superseded_by"] = "rule:verify-source:v2"
    return knowledge


@pytest.mark.parametrize("status,event_name", [
    ("specialized", "specialized"),
    ("superseded", "superseded"),
    ("retired", "retired"),
])
def test_exact_historical_lifecycle_event_matches_runtime_and_schema(status, event_name):
    knowledge = _historical_knowledge(status, event_name)
    before = deepcopy(knowledge)
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/aef/schemas/knowledge.schema.json").read_text(encoding="utf-8")
    )

    assert validate_knowledge_state(knowledge) is knowledge
    draft202012_validator(schema).validate(knowledge)
    assert knowledge == before


@pytest.mark.parametrize("status,event_name", [
    ("specialized", "specialized"),
    ("superseded", "superseded"),
    ("retired", "retired"),
])
@pytest.mark.parametrize("mutation", [
    pytest.param(lambda event: event.update(unexpected=True), id="unexpected"),
    pytest.param(lambda event: event.update(action="retire"), id="action"),
    pytest.param(lambda event: event.update(review_id=None), id="null-review-id"),
    pytest.param(lambda event: event.update(rule_id="rule:verify-source"), id="rule-id"),
    pytest.param(lambda event: event.update(replacement_id="rule:v2"), id="replacement-id"),
    pytest.param(lambda event: event.update(context={"channel": "email"}), id="context"),
    pytest.param(
        lambda event: event.update(evidence_ids=[{"id": "observation:one", "unexpected": True}]),
        id="nested-unknown",
    ),
])
def test_historical_lifecycle_event_rejects_every_noncanonical_property(status, event_name, mutation):
    knowledge = _historical_knowledge(status, event_name)
    event = knowledge["rules"][0]["lifecycle"][event_name]
    mutation(event)
    before = deepcopy(knowledge)
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/aef/schemas/knowledge.schema.json").read_text(encoding="utf-8")
    )

    with pytest.raises(InvalidKnowledgeStateError):
        validate_knowledge_state(knowledge)
    assert list(draft202012_validator(schema).iter_errors(knowledge))
    assert knowledge == before


@pytest.mark.parametrize("status,event_name,action", [
    ("specialized", "specialized", "specialize"),
    ("superseded", "superseded", "supersede"),
    ("retired", "retired", "retire"),
])
def test_exact_parent_reviewed_lifecycle_traces_are_accepted(status, event_name, action):
    knowledge = _knowledge()
    rule = knowledge["rules"][0]
    rule["status"] = status
    event = _legacy_event(action)
    rule["lifecycle"] = {event_name: event}
    if status == "specialized":
        rule["context"] = deepcopy(event["context"])
    elif status == "superseded":
        rule["superseded_by"] = event["replacement_id"]
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/aef/schemas/knowledge.schema.json").read_text(encoding="utf-8")
    )

    assert validate_knowledge_state(knowledge) is knowledge
    jsonschema.Draft202012Validator(schema).validate(knowledge)


def test_reusing_legacy_review_identity_is_blocked_without_state_change():
    knowledge = _knowledge()
    rule = knowledge["rules"][0]
    rule.update(
        status="specialized", context={"record_type": "ambiguous"},
        lifecycle={"specialized": _legacy_event("specialize")},
    )
    project = _project(knowledge)
    review = _specialize_review()
    review["id"] = "review:legacy:specialize"

    status, out, meta = consolidate_project(
        project, {"protocol": "aef.consolidate/v1", "reviews": [review]}
    )

    assert status == "BLOCKED" and out == project
    assert meta["reason"] == "legacy_review_identity_unverifiable"


def test_distinct_review_can_retire_a_legacy_specialized_rule():
    knowledge = _knowledge()
    rule = knowledge["rules"][0]
    rule.update(
        status="specialized", context={"record_type": "ambiguous"},
        lifecycle={"specialized": _legacy_event("specialize")},
    )
    retire = {
        "id": "review:new:retire", "rule_id": rule["id"], "action": "retire",
        "reason": "The specialized rule is no longer supported.",
        "evidence_ids": ["observation:one"], "approval": _approval(),
    }

    status, out, _ = consolidate_project(
        _project(knowledge), {"protocol": "aef.consolidate/v1", "reviews": [retire]}
    )

    assert status == "CHANGE"
    changed = out["files"][KNOWLEDGE_PATH]["rules"][0]
    assert changed["status"] == "retired"
    assert changed["lifecycle"]["specialized"] == _legacy_event("specialize")


def test_legacy_no_change_round_trip_preserves_exact_bytes(tmp_path):
    workspace = tmp_path / "workspace"
    knowledge = _knowledge()
    knowledge["rules"][0].update(
        status="retired", lifecycle={"retired": _legacy_event("retire")}
    )
    apply_workspace(workspace, load_workspace(workspace), _project(knowledge))
    target = workspace / KNOWLEDGE_PATH
    original = target.read_bytes()
    current = load_workspace(workspace)

    status, desired, _ = consolidate_project(
        current, {"protocol": "aef.consolidate/v1", "reviews": []}
    )
    diff = apply_workspace(workspace, current, desired)

    assert status == "NO_CHANGE"
    assert diff == {"created": [], "modified": [], "removed": []}
    assert target.read_bytes() == original


@pytest.mark.parametrize("collision", [
    "review_equals_rule", "review_equals_replacement", "replacement_equals_other_review",
])
def test_cross_category_consolidation_identifiers_are_rejected(collision):
    supersede = {
        "id": "review:supersede", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "Replacement reviewed.",
        "evidence_ids": ["observation:one"], "approval": _approval(),
        "replacement": {
            "id": "rule:v2", "type": "rule", "status": "active",
            "pattern_key": "v2", "evidence_ids": ["observation:one"],
        },
    }
    reviews = [supersede]
    if collision == "review_equals_rule":
        supersede["id"] = supersede["rule_id"]
    elif collision == "review_equals_replacement":
        supersede["id"] = supersede["replacement"]["id"]
    else:
        keep = {
            "id": supersede["replacement"]["id"], "rule_id": "rule:other",
            "action": "keep", "reason": "Keep.", "evidence_ids": [],
        }
        reviews.append(keep)

    with pytest.raises(InvalidConsolidationInputError):
        validate_consolidation_document(
            {"protocol": "aef.consolidate/v1", "reviews": reviews}
        )


def test_last_cross_collision_in_batch_prevents_every_transition():
    project = _project()
    project["files"][KNOWLEDGE_PATH]["rules"].append({
        "id": "rule:other", "type": "rule", "status": "active",
        "pattern_key": "other", "evidence_ids": ["observation:one"],
    })
    first = {
        "id": "review:retire:first", "rule_id": "rule:verify-source",
        "action": "retire", "reason": "Retire first.",
        "evidence_ids": ["observation:one"], "approval": _approval(),
    }
    last = {
        "id": "rule:other", "rule_id": "rule:other", "action": "retire",
        "reason": "Invalid colliding review.", "evidence_ids": ["observation:one"],
        "approval": _approval(),
    }
    before = deepcopy(project)

    with pytest.raises(InvalidConsolidationInputError):
        consolidate_project(
            project, {"protocol": "aef.consolidate/v1", "reviews": [first, last]}
        )
    assert project == before


def test_official_profile_has_canonical_persistent_knowledge_state():
    state = get_init_profile("aef-v1")["initial_files"][KNOWLEDGE_PATH]

    assert state == {
        "signals": [],
        "observations": [],
        "hypotheses": [],
        "rules": [],
        "principles": [],
        "mistakes": [],
    }
    assert validate_knowledge_state(state) is state


def test_legacy_knowledge_shape_is_valid_without_normalization():
    legacy = {"observations": [], "hypotheses": [], "rules": [], "mistakes": []}
    before = deepcopy(legacy)

    assert validate_knowledge_state(legacy) is legacy
    assert legacy == before


def test_complete_knowledge_validator_rejects_unknown_root_and_bad_lifecycle():
    unknown = _knowledge()
    unknown["extension"] = {}
    with pytest.raises(InvalidKnowledgeStateError):
        validate_knowledge_state(unknown)

    retired = _knowledge()
    retired["rules"][0]["status"] = "retired"
    with pytest.raises(InvalidKnowledgeStateError):
        validate_knowledge_state(retired)


def test_consolidation_review_protocol_requires_explicit_human_approval():
    document = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    before = deepcopy(document)

    validated = validate_consolidation_document(document)

    assert validated == document == before
    assert validated is not document


@pytest.mark.parametrize("change", [
    lambda review: review.pop("approval"),
    lambda review: review["approval"].update(approved=False),
    lambda review: review["approval"].update(source="agent"),
    lambda review: review["approval"].update(actor=" "),
    lambda review: review["approval"].update(approved_at="2026-08-14T14:00:00"),
])
def test_modifying_review_rejects_missing_or_invalid_approval(change):
    review = _specialize_review()
    change(review)
    with pytest.raises(InvalidConsolidationInputError):
        validate_consolidation_document({"protocol": "aef.consolidate/v1", "reviews": [review]})


def test_keep_forbids_approval_and_duplicate_rule_reviews_are_rejected():
    keep = {
        "id": "review:keep:verify-source", "rule_id": "rule:verify-source",
        "action": "keep", "reason": "No contradictory evidence.", "evidence_ids": [],
    }
    assert validate_consolidation_document(
        {"protocol": "aef.consolidate/v1", "reviews": [keep]}
    )["reviews"] == [keep]
    with_approval = deepcopy(keep)
    with_approval["approval"] = _approval()
    with pytest.raises(InvalidConsolidationInputError):
        validate_consolidation_document(
            {"protocol": "aef.consolidate/v1", "reviews": [with_approval]}
        )
    duplicate = deepcopy(keep)
    duplicate["id"] = "review:keep:duplicate"
    with pytest.raises(InvalidConsolidationInputError):
        validate_consolidation_document(
            {"protocol": "aef.consolidate/v1", "reviews": [keep, duplicate]}
        )


def test_project_consolidation_persists_traceability_and_replays_without_change():
    project = _project()
    before = deepcopy(project)
    document = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}

    status, once, meta = consolidate_project(project, document)
    replay_status, twice, replay_meta = consolidate_project(once, document)

    assert status == "CHANGE"
    assert replay_status == "NO_CHANGE"
    assert twice == once
    assert project == before
    event = once["files"][KNOWLEDGE_PATH]["rules"][0]["lifecycle"]["specialized"]
    assert event == {
        "review_id": "review:specialize:verify-source",
        "rule_id": "rule:verify-source",
        "action": "specialize",
        "reason": "Only ambiguous records need extra verification.",
        "evidence_ids": ["observation:one", "observation:two"],
        "approval": _approval(),
        "context": {"record_type": "ambiguous"},
    }
    assert meta["authority_granted"] is False
    assert replay_meta["decisions"][0]["decision"] == "NO_CHANGE"


def test_consolidation_blocks_missing_and_ambiguous_evidence_for_complete_lot():
    document = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    missing = _project()
    missing["files"][KNOWLEDGE_PATH]["observations"].pop()
    status, out, meta = consolidate_project(missing, document)
    assert status == "BLOCKED" and out == missing
    assert meta["reason"] == "missing_evidence_reference"

    ambiguous = _project()
    ambiguous["files"][KNOWLEDGE_PATH]["signals"] = [{
        "id": "observation:one", "type": "signal", "status": "candidate",
    }]
    status, out, meta = consolidate_project(ambiguous, document)
    assert status == "BLOCKED" and out == ambiguous
    assert meta["reason"] == "ambiguous_evidence_reference"


@pytest.mark.parametrize("ambiguous", [False, True])
def test_keep_evidence_references_are_validated(ambiguous):
    project = _project()
    evidence_id = "missing:evidence"
    if ambiguous:
        evidence_id = "observation:one"
        project["files"][KNOWLEDGE_PATH]["signals"] = [{
            "id": evidence_id, "type": "signal", "status": "candidate",
        }]
    keep = {
        "id": "review:keep:verify-source", "rule_id": "rule:verify-source",
        "action": "keep", "reason": "The existing rule remains supported.",
        "evidence_ids": [evidence_id],
    }
    before = deepcopy(project)

    status, out, meta = consolidate_project(
        project, {"protocol": "aef.consolidate/v1", "reviews": [keep]}
    )

    assert status == "BLOCKED"
    assert out == before == project
    assert meta["reason"] == (
        "ambiguous_evidence_reference" if ambiguous else "missing_evidence_reference"
    )


def test_keep_with_one_resolved_evidence_is_no_change():
    project = _project()
    keep = {
        "id": "review:keep:verify-source", "rule_id": "rule:verify-source",
        "action": "keep", "reason": "The rule remains supported.",
        "evidence_ids": ["observation:one"],
    }

    status, out, meta = consolidate_project(
        project, {"protocol": "aef.consolidate/v1", "reviews": [keep]}
    )

    assert status == "NO_CHANGE"
    assert out == project
    assert meta["decisions"][0]["decision"] == "KEEP"


def test_replacement_identifier_cannot_collide_with_another_knowledge_collection():
    project = _project()
    project["files"][KNOWLEDGE_PATH]["signals"] = [{
        "id": "rule:verify-source:v2", "type": "signal", "status": "candidate",
    }]
    review = {
        "id": "review:supersede:verify-source", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "A replacement was reviewed.",
        "evidence_ids": ["observation:one", "observation:two"],
        "approval": _approval(),
        "replacement": {
            "id": "rule:verify-source:v2", "type": "rule", "status": "active",
            "pattern_key": "verify-source-v2",
            "evidence_ids": ["observation:one", "observation:two"],
        },
    }
    before = deepcopy(project)

    status, out, meta = consolidate_project(
        project, {"protocol": "aef.consolidate/v1", "reviews": [review]}
    )

    assert status == "BLOCKED"
    assert out == before == project
    assert meta["reason"] == "knowledge_identifier_conflict"


def test_replacement_identifier_cannot_collide_with_a_principle():
    project = _project()
    project["files"][KNOWLEDGE_PATH]["principles"] = [{
        "id": "rule:verify-source:v2", "type": "principle", "status": "active",
        "derived_from": "rule:verify-source", "human_approved": True,
    }]
    review = {
        "id": "review:supersede:verify-source", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "A replacement was reviewed.",
        "evidence_ids": ["observation:one", "observation:two"],
        "approval": _approval(),
        "replacement": {
            "id": "rule:verify-source:v2", "type": "rule", "status": "active",
            "pattern_key": "verify-source-v2",
            "evidence_ids": ["observation:one", "observation:two"],
        },
    }

    status, out, meta = consolidate_project(
        project, {"protocol": "aef.consolidate/v1", "reviews": [review]}
    )

    assert status == "BLOCKED" and out == project
    assert meta["reason"] == "knowledge_identifier_conflict"


def test_supersede_and_retire_preserve_rules_and_approval_history():
    supersede = {
        "id": "review:supersede:verify-source", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "A narrower replacement is validated.",
        "evidence_ids": ["observation:one", "observation:two"],
        "approval": _approval(),
        "replacement": {
            "id": "rule:verify-source:v2", "type": "rule", "status": "active",
            "pattern_key": "verify-source-v2",
            "evidence_ids": ["observation:one", "observation:two"],
        },
    }
    status, replaced, _ = consolidate_project(
        _project(), {"protocol": "aef.consolidate/v1", "reviews": [supersede]}
    )
    rules = replaced["files"][KNOWLEDGE_PATH]["rules"]
    assert status == "CHANGE"
    assert {rule["id"] for rule in rules} == {"rule:verify-source", "rule:verify-source:v2"}
    old = next(rule for rule in rules if rule["id"] == "rule:verify-source")
    assert old["status"] == "superseded"
    assert old["lifecycle"]["superseded"]["approval"] == _approval()
    assert old["lifecycle"]["superseded"]["replacement"] == supersede["replacement"]

    retire = deepcopy(_specialize_review())
    retire.update(id="review:retire:verify-source", action="retire")
    retire.pop("context")
    status, retired, _ = consolidate_project(
        _project(), {"protocol": "aef.consolidate/v1", "reviews": [retire]}
    )
    assert status == "CHANGE"
    assert retired["files"][KNOWLEDGE_PATH]["rules"][0]["status"] == "retired"


def test_supersede_replay_conflicts_when_replacement_content_changes():
    review = {
        "id": "review:supersede:verify-source", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "A replacement was reviewed.",
        "evidence_ids": ["observation:one", "observation:two"],
        "approval": _approval(),
        "replacement": {
            "id": "rule:verify-source:v2", "type": "rule", "status": "active",
            "pattern_key": "verify-source-v2",
            "evidence_ids": ["observation:one", "observation:two"],
        },
    }
    document = {"protocol": "aef.consolidate/v1", "reviews": [review]}
    _, once, _ = consolidate_project(_project(), document)
    changed = deepcopy(document)
    changed["reviews"][0]["replacement"]["pattern_key"] = "different-pattern"

    status, out, meta = consolidate_project(once, changed)

    assert status == "BLOCKED" and out == once
    assert meta["reason"] == "review_id_conflict"


def test_reordered_review_and_replacement_evidence_replays_as_no_change():
    review = {
        "id": "review:supersede:verify-source", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "A replacement was reviewed.",
        "evidence_ids": ["observation:two", "observation:one"],
        "approval": _approval(),
        "replacement": {
            "id": "rule:verify-source:v2", "type": "rule", "status": "active",
            "pattern_key": "verify-source-v2",
            "evidence_ids": ["observation:one", "observation:two"],
        },
    }
    original = deepcopy(review)
    document = {"protocol": "aef.consolidate/v1", "reviews": [review]}
    _, once, _ = consolidate_project(_project(), document)
    reordered = deepcopy(document)
    reordered["reviews"][0]["evidence_ids"].reverse()
    reordered["reviews"][0]["replacement"]["evidence_ids"].reverse()

    status, out, meta = consolidate_project(once, reordered)

    assert status == "NO_CHANGE" and out == once
    assert meta["decisions"][0]["decision"] == "NO_CHANGE"
    assert review == original


def test_invalid_second_specialization_blocks_other_valid_review_in_same_batch():
    first = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    _, once, _ = consolidate_project(_project(), first)
    second_rule = {
        "id": "rule:other", "type": "rule", "status": "active",
        "pattern_key": "other", "evidence_ids": ["observation:one"],
    }
    once["files"][KNOWLEDGE_PATH]["rules"].append(second_rule)
    retire = {
        "id": "review:retire:other", "rule_id": "rule:other", "action": "retire",
        "reason": "The rule is obsolete.", "evidence_ids": ["observation:one"],
        "approval": _approval(),
    }
    second = _specialize_review()
    second["id"] = "review:specialize:second"
    before = deepcopy(once)

    status, out, meta = consolidate_project(
        once, {"protocol": "aef.consolidate/v1", "reviews": [retire, second]}
    )

    assert status == "BLOCKED"
    assert out == before == once
    assert meta["reason"] == "rule_already_specialized"


@pytest.mark.parametrize("mutation", [
    lambda replacement: replacement.pop("type"),
    lambda replacement: replacement.update(type="principle"),
    lambda replacement: replacement.update(status="candidate"),
    lambda replacement: replacement.update(pattern_key=" "),
    lambda replacement: replacement.update(evidence_ids=["observation:one"]),
    lambda replacement: replacement.update(extension=True),
])
def test_supersede_requires_a_complete_canonical_replacement(mutation):
    review = {
        "id": "review:supersede:verify-source", "rule_id": "rule:verify-source",
        "action": "supersede", "reason": "A replacement was reviewed.",
        "evidence_ids": ["observation:one", "observation:two"],
        "approval": _approval(),
        "replacement": {
            "id": "rule:verify-source:v2", "type": "rule", "status": "active",
            "pattern_key": "verify-source-v2",
            "evidence_ids": ["observation:one", "observation:two"],
        },
    }
    mutation(review["replacement"])

    with pytest.raises(InvalidConsolidationInputError):
        validate_consolidation_document(
            {"protocol": "aef.consolidate/v1", "reviews": [review]}
        )


def test_reused_review_id_with_different_content_blocks_without_mutation():
    document = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    _, once, _ = consolidate_project(_project(), document)
    conflict = deepcopy(document)
    conflict["reviews"][0]["reason"] = "Different reason."

    status, out, meta = consolidate_project(once, conflict)

    assert status == "BLOCKED"
    assert out == once
    assert meta["reason"] == "review_id_conflict"


def test_review_identity_is_bound_to_owning_rule():
    document = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    _, once, _ = consolidate_project(_project(), document)
    knowledge = once["files"][KNOWLEDGE_PATH]
    original = knowledge["rules"][0]
    impostor = deepcopy(original)
    impostor["id"] = "rule:other"
    impostor["lifecycle"]["specialized"]["rule_id"] = "rule:other"
    knowledge["rules"] = [impostor, _rule()]
    before = deepcopy(once)

    status, out, meta = consolidate_project(once, document)

    assert status == "BLOCKED"
    assert out == before == once
    assert meta["reason"] == "review_id_conflict"


def test_second_specialization_is_blocked_and_first_trace_is_preserved():
    first = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    _, once, _ = consolidate_project(_project(), first)
    original_event = deepcopy(
        once["files"][KNOWLEDGE_PATH]["rules"][0]["lifecycle"]["specialized"]
    )
    second_review = _specialize_review()
    second_review.update(
        id="review:specialize:verify-source:second",
        reason="A different context was proposed.",
        context={"record_type": "financial"},
    )

    status, out, meta = consolidate_project(
        once, {"protocol": "aef.consolidate/v1", "reviews": [second_review]}
    )

    assert status == "BLOCKED"
    assert out == once
    assert meta["reason"] == "rule_already_specialized"
    assert out["files"][KNOWLEDGE_PATH]["rules"][0]["lifecycle"]["specialized"] == original_event


def test_cli_consolidate_dry_run_apply_replay_and_human_output(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    apply_workspace(workspace, load_workspace(workspace), _project())
    review_path = tmp_path / "reviews.json"
    review_path.write_text(
        json.dumps({"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}),
        encoding="utf-8",
    )

    dry_code = cli.main([
        "--json", "--workspace", str(workspace), "consolidate",
        "--reviews", str(review_path), "--dry-run",
    ])
    dry = json.loads(capsys.readouterr().out)
    apply_code = cli.main([
        "--json", "--workspace", str(workspace), "consolidate",
        "--reviews", str(review_path),
    ])
    applied = json.loads(capsys.readouterr().out)
    replay_code = cli.main([
        "--human", "--workspace", str(workspace), "consolidate",
        "--reviews", str(review_path),
    ])
    human = capsys.readouterr().out

    assert (dry_code, apply_code, replay_code) == (0, 0, 0)
    assert dry["status"] == applied["status"] == "CHANGE"
    assert dry["diff"] == applied["diff"]
    assert human.startswith("[OK] AEF knowledge needs no consolidation\n")


def test_consolidation_pipeline_preserves_every_unrelated_state_and_is_strict_json():
    project = _project()
    project["files"][KNOWLEDGE_PATH]["mistakes"] = [{
        "id": "mistake:one", "type": "mistake", "status": "active",
        "summary": "Preserve me.",
    }]
    before = deepcopy(project)
    status, out, _ = consolidate_project(
        project, {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    )
    knowledge = out["files"][KNOWLEDGE_PATH]
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/aef/schemas/knowledge.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert status == "CHANGE"
    assert project == before
    assert knowledge["signals"] == before["files"][KNOWLEDGE_PATH]["signals"]
    assert knowledge["observations"] == before["files"][KNOWLEDGE_PATH]["observations"]
    assert knowledge["hypotheses"] == before["files"][KNOWLEDGE_PATH]["hypotheses"]
    assert knowledge["principles"] == before["files"][KNOWLEDGE_PATH]["principles"]
    assert knowledge["mistakes"] == before["files"][KNOWLEDGE_PATH]["mistakes"]
    assert out["files"][".agent/integrations/registry.json"] == {"connectors": []}
    validate_knowledge_state(knowledge)
    jsonschema.Draft202012Validator(schema).validate(knowledge)
    json.dumps(knowledge, sort_keys=True, allow_nan=False)


def test_lifecycle_schema_accepts_canonical_trace_and_rejects_invalid_trace_fields():
    _, out, _ = consolidate_project(
        _project(),
        {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]},
    )
    knowledge = out["files"][KNOWLEDGE_PATH]
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/aef/schemas/knowledge.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = draft202012_validator(schema)

    validator.validate(knowledge)
    bad_approval = deepcopy(knowledge)
    bad_approval["rules"][0]["lifecycle"]["specialized"]["approval"]["source"] = "agent"
    assert list(validator.iter_errors(bad_approval))
    unknown = deepcopy(knowledge)
    unknown["rules"][0]["lifecycle"]["specialized"]["technical"] = "leak"
    assert list(validator.iter_errors(unknown))

    impossible = deepcopy(knowledge)
    impossible["rules"][0]["lifecycle"]["specialized"]["approval"]["approved_at"] = (
        "2026-02-30T14:00:00Z"
    )
    assert list(validator.iter_errors(impossible))


def test_lifecycle_schema_preserves_legacy_events_without_review_identity():
    knowledge = _knowledge()
    knowledge["rules"][0].update({
        "status": "retired",
        "lifecycle": {"retired": {"reason": "Legacy retirement", "evidence_ids": []}},
    })
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "src/aef/schemas/knowledge.schema.json").read_text(
            encoding="utf-8"
        )
    )

    validate_knowledge_state(knowledge)
    jsonschema.Draft202012Validator(schema).validate(knowledge)


def test_dry_plan_bytes_equal_real_application_and_only_knowledge_changes(tmp_path):
    workspace = tmp_path / "workspace"
    current_project = _project()
    apply_workspace(workspace, load_workspace(workspace), current_project)
    current = load_workspace(workspace)
    document = {"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}
    status, desired, _ = consolidate_project(current, document)
    diff, planned = render_workspace_plan(current, desired)

    applied_diff = apply_workspace(workspace, current, desired)
    target = workspace / KNOWLEDGE_PATH

    assert status == "CHANGE"
    assert diff == applied_diff == {
        "created": [], "modified": [KNOWLEDGE_PATH], "removed": []
    }
    assert target.read_bytes() == planned[KNOWLEDGE_PATH].encode("utf-8")
    assert not list((workspace / ".agent").rglob("*.tmp"))


def test_invalid_persisted_review_trace_is_rejected_without_mutation():
    project = _project()
    project["files"][KNOWLEDGE_PATH]["rules"][0].update({
        "status": "retired",
        "lifecycle": {"retired": {
            "review_id": "review:bad", "action": "retire", "reason": "x",
            "evidence_ids": [],
            "approval": {"approved": True, "source": "agent", "actor": "x", "approved_at": "bad"},
        }},
    })
    before = deepcopy(project)

    with pytest.raises(InvalidKnowledgeStateError):
        consolidate_project(
            project, {"protocol": "aef.consolidate/v1", "reviews": []}
        )
    assert project == before


@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_consolidate_subprocess_modes_and_launchers(tmp_path, mode):
    workspace = tmp_path / f"workspace-{mode}"
    apply_workspace(workspace, load_workspace(workspace), _project())
    reviews = tmp_path / f"reviews-{mode}.json"
    reviews.write_text(
        json.dumps({"protocol": "aef.consolidate/v1", "reviews": [_specialize_review()]}),
        encoding="utf-8",
    )
    python = Path(sys.executable)
    prefix = [str(python), "-m", "aef"]
    option = "--human" if mode == "human" else f"--{mode}"

    completed = subprocess.run(
        [*prefix, option, "--workspace", str(workspace), "consolidate", "--reviews", str(reviews)],
        input="", capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0
    assert "Traceback" not in completed.stdout + completed.stderr
    if mode == "human":
        assert completed.stdout.startswith("[OK] AEF knowledge consolidated\n")
        assert "\"api_version\"" not in completed.stdout
    else:
        envelope = json.loads(completed.stdout)
        assert envelope["command"] == "CONSOLIDATE"
        assert envelope["status"] == "CHANGE"
        assert envelope["result"]["authority_granted"] is False
        if mode == "compact":
            assert completed.stdout.count("\n") == 1


def test_cli_classifies_invalid_review_and_knowledge_documents(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    apply_workspace(workspace, load_workspace(workspace), _project())
    invalid_review = tmp_path / "invalid-review.json"
    invalid_review.write_text('{"protocol":"aef.consolidate/v1","reviews":[{}]}', encoding="utf-8")
    code = cli.main([
        "--json", "--workspace", str(workspace), "consolidate", "--reviews", str(invalid_review)
    ])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["code"] == "invalid_consolidation_input"

    (workspace / KNOWLEDGE_PATH).write_text('{"observations":[],"hypotheses":[],"rules":[]}', encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text('{"protocol":"aef.consolidate/v1","reviews":[]}', encoding="utf-8")
    code = cli.main([
        "--compact", "--workspace", str(workspace), "consolidate", "--reviews", str(empty)
    ])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["code"] == "invalid_knowledge_state"


def test_existing_consolidation_keep_is_strictly_read_only():
    state = _knowledge()
    before = deepcopy(state)

    status, out, decisions = consolidate_knowledge(
        state,
        rule_reviews=[{"rule_id": "rule:verify-source", "contradictions": 0}],
    )

    assert status == "NO_CHANGE"
    assert out == before == state
    assert decisions == [{
        "rule_id": "rule:verify-source",
        "decision": "KEEP",
        "affected": "rule:verify-source",
    }]


def test_existing_consolidation_specializes_without_mutating_source():
    state = _knowledge()
    before = deepcopy(state)
    review = {
        "rule_id": "rule:verify-source",
        "contradictions": 1,
        "contexts": [{"record_type": "ambiguous"}],
        "reason": "Only ambiguous records need extra verification.",
        "evidence_ids": ["observation:one", "observation:two"],
    }

    status, out, decisions = consolidate_knowledge(state, rule_reviews=[review])

    assert status == "CHANGE"
    assert state == before
    assert out["observations"] == before["observations"]
    assert out["rules"][0]["id"] == "rule:verify-source"
    assert out["rules"][0]["status"] == "specialized"
    assert decisions[0]["decision"] == "SPECIALIZE"
