"""Voie B — explicit human validation of candidate hypotheses."""

from __future__ import annotations

import json

from aef import cli
from aef.record_document import build_persisted_record
from tests.test_cli_ingest import init_workspace, invoke, persist_sample_record, submission, write_json


PATTERN = "init-dry-run"
HYPOTHESIS_ID = f"hypothesis:{PATTERN}"


def validation_for(**overrides):
    document = {
        "protocol": "aef.learning.validate.submit/v1",
        "hypotheses": [HYPOTHESIS_ID],
        "records": [],
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    }
    document.update(overrides)
    return document


def bootstrap_hypothesis(tmp_path, capsys):
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(
        tmp_path / "bootstrap.json",
        {
            "protocol": "aef.ingest.submit/v1",
            "records": [{
                "record_id": persisted["record_id"],
                "digest": persisted["digest"],
                "events": [
                    {"id": "h1", "kind": "help_request", "pattern_key": PATTERN},
                    {"id": "h2", "kind": "help_request", "pattern_key": PATTERN},
                    {"id": "h3", "kind": "help_request", "pattern_key": PATTERN},
                    {"id": "n1", "novel": True, "pattern_key": PATTERN},
                ],
            }],
        },
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = next(item for item in knowledge["hypotheses"] if item["id"] == HYPOTHESIS_ID)
    assert hypothesis["confirmations"] == 0
    return hypothesis


def test_validation_sets_explicit_flag_without_incrementing_confirmations(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    bootstrap_hypothesis(tmp_path, capsys)
    validation = write_json(
        tmp_path / "validation.json", validation_for(records=[]),
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert code == 0
    assert envelope["command"] == "LEARNING_VALIDATE"
    assert envelope["status"] == "CHANGE"
    assert envelope["result"]["validated"] == [HYPOTHESIS_ID]
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = next(item for item in knowledge["hypotheses"] if item["id"] == HYPOTHESIS_ID)
    assert hypothesis["explicit_human_validation"] is True
    assert hypothesis["confirmations"] == 0
    assert knowledge["rules"] == []


def test_validation_without_human_decision_is_error(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    bootstrap_hypothesis(tmp_path, capsys)
    validation = write_json(
        tmp_path / "validation.json",
        {
            "protocol": "aef.learning.validate.submit/v1",
            "hypotheses": [HYPOTHESIS_ID],
            "decision": {
                "source": "agent",
                "actor": "operator",
                "decided_at": "2026-08-21T10:00:00Z",
                "approved": True,
            },
        },
    )
    knowledge_before = (
        tmp_path / ".agent/knowledge/knowledge.json"
    ).read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["error"]["code"] == "invalid_validation"
    assert (tmp_path / ".agent/knowledge/knowledge.json").read_bytes() == knowledge_before


def test_validation_rejects_missing_hypothesis(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    validation = write_json(
        tmp_path / "validation.json",
        validation_for(hypotheses=["hypothesis:missing-pattern"]),
    )
    knowledge_before = (
        tmp_path / ".agent/knowledge/knowledge.json"
    ).read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["error"]["code"] == "hypothesis_not_found"
    assert (tmp_path / ".agent/knowledge/knowledge.json").read_bytes() == knowledge_before


def test_validation_rejects_promoted_hypothesis(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    bootstrap_hypothesis(tmp_path, capsys)
    knowledge_path = tmp_path / ".agent/knowledge/knowledge.json"
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    knowledge["rules"] = [{
        "id": f"rule:{PATTERN}",
        "type": "rule",
        "status": "active",
        "pattern_key": PATTERN,
        "derived_from": HYPOTHESIS_ID,
        "evidence_ids": [],
    }]
    knowledge_path.write_text(json.dumps(knowledge), encoding="utf-8")
    validation = write_json(tmp_path / "validation.json", validation_for())
    before = knowledge_path.read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["error"]["code"] == "hypothesis_already_promoted"
    assert knowledge_path.read_bytes() == before


def test_replay_validation_is_no_change(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    bootstrap_hypothesis(tmp_path, capsys)
    validation = write_json(tmp_path / "validation.json", validation_for())
    first_code, _, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert first_code == 0
    before = (tmp_path / ".agent/knowledge/knowledge.json").read_bytes()
    replay_code, replay_env, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    assert replay_code == 0 and replay_env["status"] == "NO_CHANGE"
    assert (tmp_path / ".agent/knowledge/knowledge.json").read_bytes() == before


def test_ingest_then_validate_does_not_double_count_confirmations(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = build_persisted_record(submission(record_id="session-alpha"))
    recording = write_json(tmp_path / "recording.json", submission(record_id="session-alpha"))
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    intake = write_json(
        tmp_path / "intake.json",
        {
            "protocol": "aef.ingest.submit/v1",
            "records": [{
                "record_id": persisted["record_id"],
                "digest": persisted["digest"],
                "events": [
                    {"id": "n1", "novel": True, "pattern_key": PATTERN},
                    {"id": "c1", "kind": "human_correction", "pattern_key": PATTERN},
                    {"id": "c2", "kind": "human_correction", "pattern_key": PATTERN},
                ],
            }],
        },
    )
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = next(item for item in knowledge["hypotheses"] if item["id"] == HYPOTHESIS_ID)
    assert hypothesis["confirmations"] == 1
    validation = write_json(tmp_path / "validation.json", validation_for())
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "learning", "validate", "--validation", str(validation),
    )
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = next(item for item in knowledge["hypotheses"] if item["id"] == HYPOTHESIS_ID)
    assert hypothesis["explicit_human_validation"] is True
    assert hypothesis["confirmations"] == 1
    assert knowledge["rules"] == []
