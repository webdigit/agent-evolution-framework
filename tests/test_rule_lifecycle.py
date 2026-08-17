from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from aef.rule_lifecycle import (
    applicable_rules, specialize_rule, supersede_rule, retire_rule, review_rule
)


ROOT = Path(__file__).resolve().parents[1]


def base_rule():
    return {
        "id": "rule:response-tone",
        "type": "rule",
        "status": "active",
        "pattern_key": "response-tone",
        "evidence_ids": ["obs:1", "obs:2", "obs:3"],
    }


def test_active_rule_is_applicable():
    assert [r["id"] for r in applicable_rules([base_rule()], context={})] == ["rule:response-tone"]


def test_specialization_limits_context_and_preserves_identity():
    status, rules = specialize_rule(
        [base_rule()], rule_id="rule:response-tone", context={"channel": "email"},
        reason="chat evidence diverged", evidence_ids=["obs:4"]
    )
    assert status == "CHANGE"
    assert rules[0]["id"] == "rule:response-tone"
    assert rules[0]["status"] == "specialized"
    assert applicable_rules(rules, context={"channel": "chat"}) == []
    assert applicable_rules(rules, context={"channel": "email"})[0]["id"] == "rule:response-tone"


def test_specialization_replay_is_no_change():
    _, once = specialize_rule([base_rule()], rule_id="rule:response-tone", context={"channel": "email"}, reason="x")
    status, twice = specialize_rule(once, rule_id="rule:response-tone", context={"channel": "email"}, reason="x")
    assert status == "NO_CHANGE"
    assert twice == once


def test_specialize_rule_output_validates_against_knowledge_schema_without_mutating_inputs():
    rules = [base_rule()]
    context = {"channel": "email"}
    evidence_ids = ["obs:4", "obs:4", "obs:5"]
    inputs_before = deepcopy((rules, context, evidence_ids))

    status, specialized = specialize_rule(
        rules,
        rule_id="rule:response-tone",
        context=context,
        reason="Chat evidence diverged",
        evidence_ids=evidence_ids,
    )
    knowledge = {
        "observations": [],
        "hypotheses": [],
        "rules": specialized,
        "principles": [],
    }
    schema = json.loads((ROOT / "src/aef/schemas/knowledge.schema.json").read_text(encoding="utf-8"))

    assert status == "CHANGE"
    jsonschema.Draft202012Validator(schema).validate(knowledge)
    assert (rules, context, evidence_ids) == inputs_before
    assert specialized[0]["lifecycle"]["specialized"]["evidence_ids"] == ["obs:4", "obs:5"]


@pytest.mark.parametrize(("context", "reason", "evidence_ids"), [
    ({}, "reason", []),
    ([], "reason", []),
    ({"channel": "email"}, "", []),
    ({"channel": "email"}, "   ", []),
    ({"channel": "email"}, "reason", "obs:4"),
    ({"channel": "email"}, "reason", ["obs:4", 5]),
])
def test_specialize_rule_rejects_invalid_invariants_before_mutation(context, reason, evidence_ids):
    rules = [base_rule()]
    before = deepcopy(rules)

    with pytest.raises(ValueError):
        specialize_rule(
            rules,
            rule_id="rule:response-tone",
            context=context,
            reason=reason,
            evidence_ids=evidence_ids,
        )

    assert rules == before


def test_supersession_keeps_old_rule_but_disables_it():
    replacement = {"id": "rule:response-tone:v2", "pattern_key": "response-tone-v2"}
    status, rules, new_id = supersede_rule(
        [base_rule()], rule_id="rule:response-tone", replacement=replacement,
        reason="workflow changed", evidence_ids=["obs:5", "obs:6"]
    )
    assert status == "CHANGE"
    old = next(r for r in rules if r["id"] == "rule:response-tone")
    new = next(r for r in rules if r["id"] == new_id)
    assert old["status"] == "superseded"
    assert old["superseded_by"] == new_id
    assert new["supersedes"] == old["id"]
    assert [r["id"] for r in applicable_rules(rules)] == [new_id]


def test_supersession_replay_does_not_duplicate():
    replacement = {"id": "rule:response-tone:v2", "pattern_key": "response-tone-v2"}
    _, once, _ = supersede_rule([base_rule()], rule_id="rule:response-tone", replacement=replacement, reason="workflow changed")
    status, twice, _ = supersede_rule(once, rule_id="rule:response-tone", replacement=replacement, reason="workflow changed")
    assert status == "NO_CHANGE"
    assert twice == once
    assert len(twice) == 2


def test_retired_rule_remains_in_history_but_is_not_applicable():
    status, rules = retire_rule([base_rule()], rule_id="rule:response-tone", reason="obsolete")
    assert status == "CHANGE"
    assert len(rules) == 1
    assert rules[0]["status"] == "retired"
    assert applicable_rules(rules) == []


def test_review_specializes_when_contradiction_is_contextual():
    decision, rules, _ = review_rule(
        [base_rule()], rule_id="rule:response-tone", contradictions=2,
        contexts=[{"channel": "email"}], reason="only email remains valid"
    )
    assert decision == "SPECIALIZE"
    assert rules[0]["status"] == "specialized"


def test_review_retires_only_after_repeated_unresolved_contradictions():
    decision, rules, _ = review_rule([base_rule()], rule_id="rule:response-tone", contradictions=2)
    assert decision == "KEEP_PENDING_EVIDENCE"
    assert rules == [base_rule()]
    decision, rules, _ = review_rule([base_rule()], rule_id="rule:response-tone", contradictions=3)
    assert decision == "RETIRE"
    assert rules[0]["status"] == "retired"


def test_review_with_no_contradiction_is_read_only():
    rules = [base_rule()]
    before = deepcopy(rules)
    decision, out, _ = review_rule(rules, rule_id="rule:response-tone", contradictions=0)
    assert decision == "KEEP"
    assert out == before == rules
