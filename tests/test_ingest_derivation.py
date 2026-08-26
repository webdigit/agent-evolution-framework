"""Étage 2 — rule derivation at ingest and principle derivation via validate."""

from __future__ import annotations

import json

from aef.record_document import build_persisted_record
from tests.test_cli_ingest import init_workspace, intake_for, invoke, submission, write_json
from tests.test_ingest_confirmation import PATTERN, _hypothesis, _ingest_human_correction


RULE_ID = f"rule:{PATTERN}"
HYPOTHESIS_ID = f"hypothesis:{PATTERN}"
PRINCIPLE_ID = f"principle:{PATTERN}"


def validation_for_rules(*rule_ids):
    return {
        "protocol": "aef.learning.validate.submit/v1",
        "rules": list(rule_ids),
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    }


def test_three_intakes_derive_rule_and_envelope_announces(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    last_envelope = None
    for record_id, event_id, extra in (
        ("session-one", "hc-one-a", [
            {"id": "novel-one", "novel": True, "pattern_key": PATTERN},
            {"id": "hc-one-b", "kind": "human_correction", "pattern_key": PATTERN},
        ]),
        ("session-two", "hc-two", None),
        ("session-three", "hc-three", None),
    ):
        code, envelope, _ = _ingest_human_correction(
            tmp_path, capsys,
            record_id=record_id,
            event_id=event_id,
            extra=extra,
        )
        assert code == 0 and envelope["status"] == "CHANGE"
        last_envelope = envelope

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = _hypothesis(knowledge)
    assert hypothesis["confirmations"] == 3
    assert len(knowledge["rules"]) == 1
    rule = knowledge["rules"][0]
    assert rule["id"] == RULE_ID
    assert rule["derived_from"] == HYPOTHESIS_ID
    assert rule["status"] == "active"
    assert rule["evidence_ids"]
    assert rule["evidence_ids"] == hypothesis["evidence_ids"]
    assert last_envelope is not None
    assert last_envelope["result"]["rules_derived"] == [RULE_ID]


def test_rule_evidence_ids_frozen_when_hypothesis_revises(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    for record_id, event_id, extra in (
        ("session-one", "hc-one-a", [
            {"id": "novel-one", "novel": True, "pattern_key": PATTERN},
            {"id": "hc-one-b", "kind": "human_correction", "pattern_key": PATTERN},
        ]),
        ("session-two", "hc-two", None),
        ("session-three", "hc-three", None),
    ):
        _ingest_human_correction(
            tmp_path, capsys,
            record_id=record_id, event_id=event_id, extra=extra,
        )

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    frozen_evidence = list(knowledge["rules"][0]["evidence_ids"])

    persisted = build_persisted_record(submission(record_id="session-four"))
    recording = write_json(tmp_path / "session-four.json", submission(record_id="session-four"))
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    intake = write_json(
        tmp_path / "intake-four.json",
        intake_for(
            persisted,
            events=[{"id": "novel-four", "novel": True, "pattern_key": PATTERN}],
        ),
    )
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = _hypothesis(knowledge)
    assert len(hypothesis["evidence_ids"]) >= len(frozen_evidence)
    assert knowledge["rules"][0]["evidence_ids"] == frozen_evidence


def test_human_validation_on_rule_creates_principle(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    for record_id, event_id, extra in (
        ("session-one", "hc-one-a", [
            {"id": "novel-one", "novel": True, "pattern_key": PATTERN},
            {"id": "hc-one-b", "kind": "human_correction", "pattern_key": PATTERN},
        ]),
        ("session-two", "hc-two", None),
        ("session-three", "hc-three", None),
    ):
        _ingest_human_correction(
            tmp_path, capsys,
            record_id=record_id, event_id=event_id, extra=extra,
        )

    knowledge_before = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    assert knowledge_before["principles"] == []

    validation = write_json(
        tmp_path / "principle-validation.json",
        validation_for_rules(RULE_ID),
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    assert envelope["result"]["principles_derived"] == [PRINCIPLE_ID]

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    assert len(knowledge["principles"]) == 1
    principle = knowledge["principles"][0]
    assert principle["id"] == PRINCIPLE_ID
    assert principle["derived_from"] == RULE_ID
    assert principle["human_approved"] is True


def test_principle_requires_human_validation_document(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    knowledge_path = tmp_path / ".agent/knowledge/knowledge.json"
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    knowledge.update({
        "observations": [],
        "hypotheses": [],
        "rules": [{
            "id": RULE_ID,
            "type": "rule",
            "status": "active",
            "pattern_key": PATTERN,
            "derived_from": HYPOTHESIS_ID,
            "evidence_ids": ["o1", "o2"],
        }],
        "principles": [],
    })
    knowledge_path.write_text(json.dumps(knowledge), encoding="utf-8")

    validation = write_json(
        tmp_path / "bad-validation.json",
        {
            "protocol": "aef.learning.validate.submit/v1",
            "rules": [RULE_ID],
            "decision": {
                "source": "agent",
                "actor": "operator",
                "decided_at": "2026-08-21T10:00:00Z",
                "approved": True,
            },
        },
    )
    before = knowledge_path.read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert code == 3 and envelope["status"] == "ERROR"
    assert knowledge_path.read_bytes() == before
    assert json.loads(before.decode())["principles"] == []
