import json
from pathlib import Path

from aef.competency_learning import ensure_competency
from aef.filesystem import apply_workspace, load_workspace
from aef.learning_lifecycle import derive_principle
from aef.rule_lifecycle import applicable_rules, specialize_rule


COMPETENCIES_PATH = ".agent/state/competencies.json"
KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"


def test_pilot_competencies_are_id_keyed_and_round_trip_exactly(tmp_path: Path):
    agent = {"career": {}, "competencies": {}}
    status, agent = ensure_competency(
        agent,
        "record-classification",
        title="Record classification",
        source="pilot",
    )

    competencies = agent["competencies"]
    assert status == "CHANGE"
    assert list(competencies) == ["record-classification"]
    assert competencies["record-classification"]["id"] == "record-classification"

    current = load_workspace(tmp_path)
    desired = {"files": {COMPETENCIES_PATH: competencies}}
    apply_workspace(tmp_path, current, desired)

    reloaded = load_workspace(tmp_path)
    assert reloaded["files"][COMPETENCIES_PATH] == competencies


def test_principles_are_first_class_pilot_knowledge_and_round_trip(tmp_path: Path):
    rules = [{"id": "rule:verify-source", "type": "rule", "status": "active"}]
    status, principles, principle_id = derive_principle(
        rules,
        [],
        rule_id="rule:verify-source",
        human_approved=True,
    )
    knowledge = {
        "observations": [],
        "hypotheses": [],
        "rules": rules,
        "principles": principles,
    }

    assert status == "CHANGE"
    assert principle_id == "principle:verify-source"
    assert principles == [{
        "id": "principle:verify-source",
        "type": "principle",
        "status": "active",
        "derived_from": "rule:verify-source",
        "human_approved": True,
    }]

    current = load_workspace(tmp_path)
    apply_workspace(tmp_path, current, {"files": {KNOWLEDGE_PATH: knowledge}})
    assert load_workspace(tmp_path)["files"][KNOWLEDGE_PATH] == knowledge


def test_mistakes_are_preserved_when_present_but_not_invented_when_absent(tmp_path: Path):
    knowledge = {
        "observations": [],
        "hypotheses": [],
        "rules": [],
        "principles": [],
        "mistakes": [{
            "id": "mistake:source-assumption",
            "type": "mistake",
            "status": "active",
            "summary": "A source assumption was not verified",
            "evidence_ids": ["event:1"],
        }],
    }
    target = tmp_path / KNOWLEDGE_PATH
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(knowledge), encoding="utf-8")
    original_bytes = target.read_bytes()

    loaded = load_workspace(tmp_path)

    assert loaded["files"][KNOWLEDGE_PATH] == knowledge
    assert target.read_bytes() == original_bytes

    without_mistakes = {
        "observations": [],
        "hypotheses": [],
        "rules": [],
        "principles": [],
    }
    target.write_text(json.dumps(without_mistakes), encoding="utf-8")
    loaded_without_mistakes = load_workspace(tmp_path)

    assert "mistakes" not in loaded_without_mistakes["files"][KNOWLEDGE_PATH]
    assert target.read_bytes() == json.dumps(without_mistakes).encode("utf-8")


def test_specialized_rule_carries_context_and_lifecycle_evidence():
    rule = {
        "id": "rule:response-tone",
        "type": "rule",
        "status": "active",
        "pattern_key": "response-tone",
        "evidence_ids": ["obs:1", "obs:2"],
    }

    status, rules = specialize_rule(
        [rule],
        rule_id=rule["id"],
        context={"channel": "email"},
        reason="Chat evidence diverged",
        evidence_ids=["obs:3"],
    )
    specialized = rules[0]

    assert status == "CHANGE"
    assert specialized["id"] == rule["id"]
    assert specialized["status"] == "specialized"
    assert specialized["context"] == {"channel": "email"}
    assert specialized["lifecycle"]["specialized"] == {
        "reason": "Chat evidence diverged",
        "evidence_ids": ["obs:3"],
    }
    assert applicable_rules(rules, context={"channel": "chat"}) == []
    assert applicable_rules(rules, context={"channel": "email"}) == [specialized]
