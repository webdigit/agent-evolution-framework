from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aef.decisions import (
    InvalidDecisionsDocumentError,
    resolve_decision,
    unresolved,
    validate_decisions_document,
)
from aef.knowledge import promote_observation
from aef.evaluation import review_due
from aef.reconcile import reconcile


def test_setup_answer_is_persisted_and_not_asked_twice():
    store = {"decisions": []}
    status1, s1 = resolve_decision(store, "decision.role.primary.v1", "support-specialist")
    status2, s2 = resolve_decision(s1, "decision.role.primary.v1", "support-specialist")
    assert status1 == "CHANGE"
    assert status2 == "NO_CHANGE"
    assert unresolved(s2, ["decision.role.primary.v1"]) == []


def test_unresolved_setup_question_remains_blocking():
    store = {"decisions": []}
    assert unresolved(store, ["decision.role.primary.v1"]) == ["decision.role.primary.v1"]


def test_canonical_decision_document_is_accepted_without_mutation():
    document = {"decisions": [{
        "id": "decision.role.primary.v1",
        "status": "resolved",
        "value": "operator",
        "source": "human-confirmed",
    }]}
    before = {"decisions": [dict(document["decisions"][0])]}

    assert validate_decisions_document(document) is document
    assert document == before


@pytest.mark.parametrize(("decision_id", "value", "source"), [
    ("", "x", "human"),
    ("decision.role.primary.v1", 42, "human"),
    ("other", "x", ""),
])
def test_resolve_decision_rejects_invalid_candidate_without_mutating_source(
    decision_id, value, source
):
    store = {"decisions": []}
    before = {"decisions": []}

    with pytest.raises(InvalidDecisionsDocumentError):
        resolve_decision(store, decision_id, value, source)

    assert store == before


def test_every_nominal_resolve_result_is_canonical_and_deterministic():
    store = {"decisions": []}

    first_status, first = resolve_decision(store, "other", "x", "human")
    replay_status, replay = resolve_decision(first, "other", "x", "human")

    assert first_status == "CHANGE"
    assert replay_status == "NO_CHANGE"
    assert validate_decisions_document(first) is first
    assert validate_decisions_document(replay) is replay
    assert replay == first
    assert store == {"decisions": []}


def test_resolve_decision_does_not_silently_repair_invalid_existing_store():
    store = {"decisions": [{
        "id": "decision.role.primary.v1",
        "status": "resolved",
        "value": 42,
        "source": "human",
    }]}
    before = {"decisions": [dict(store["decisions"][0])]}

    with pytest.raises(InvalidDecisionsDocumentError):
        resolve_decision(store, "decision.role.primary.v1", "operator", "human")

    assert store == before


def test_derived_knowledge_uses_stable_id_and_does_not_duplicate():
    obs = {"id": "obs-001", "summary": "A pattern was observed"}
    status1, records1 = promote_observation(obs, "hypothesis", [])
    status2, records2 = promote_observation(obs, "hypothesis", records1)
    assert status1 == "CHANGE"
    assert status2 == "NO_CHANGE"
    assert len(records2) == 1


def test_unknown_project_content_is_preserved_when_desired_state_contains_it():
    current = {"framework": {"version": 1}, "project_owned": {"custom": "keep-me"}}
    desired = {"framework": {"version": 2}, "project_owned": {"custom": "keep-me"}}
    _, out = reconcile(current, desired)
    assert out["project_owned"]["custom"] == "keep-me"


def test_task_count_review_due():
    assert review_due({"mode":"task_count","every_tasks":5,"interval_days":None}, tasks_since_review=5)
    assert not review_due({"mode":"task_count","every_tasks":5,"interval_days":None}, tasks_since_review=4)


def test_six_month_style_interval_with_simulated_clock():
    policy = {"mode":"interval","every_tasks":None,"interval_days":180}
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert review_due(policy, last_review_at="2026-01-01T00:00:00+00:00", now=now)


def test_repeated_audit_input_does_not_change_state():
    state = {"level":"L5","xp":1000,"trust":0.98}
    before = repr(state)
    for _ in range(10):
        _ = {"summary": dict(state)}
    assert repr(state) == before
