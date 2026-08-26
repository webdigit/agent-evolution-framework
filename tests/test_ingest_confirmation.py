"""Voie A — hypothesis confirmations at ingest (cycle apprentissage étage 1)."""

from __future__ import annotations

import json

from aef.learning_confirmation import apply_ingest_confirmations
from aef.record_document import build_persisted_record
from tests.test_cli_ingest import (
    init_workspace,
    intake_for,
    invoke,
    persist_sample_record,
    submission,
    write_json,
)


PATTERN = "cli-dry-run-gap"


def _hypothesis(knowledge: dict) -> dict:
    return next(
        item for item in knowledge["hypotheses"]
        if item.get("pattern_key") == PATTERN
    )


def _ingest_human_correction(tmp_path, capsys, *, record_id: str, event_id: str, extra=None):
    doc = submission(record_id=record_id)
    persisted = build_persisted_record(doc)
    recording = write_json(tmp_path / f"{record_id}.json", doc)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    events = [{
        "id": event_id,
        "kind": "human_correction",
        "pattern_key": PATTERN,
    }]
    if extra:
        events = extra + events
    intake = write_json(
        tmp_path / f"intake-{record_id}.json",
        intake_for(persisted, events=events),
    )
    return invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )


def test_success_and_help_request_do_not_increment_confirmations():
    state = {
        "signals": [],
        "observations": [],
        "hypotheses": [{
            "id": f"hypothesis:{PATTERN}",
            "type": "hypothesis",
            "status": "candidate",
            "pattern_key": PATTERN,
            "evidence_ids": ["o1", "o2"],
            "confirmations": 0,
            "explicit_human_validation": False,
        }],
        "rules": [],
        "principles": [],
    }
    citations = {
        "e-success": {"record_id": "rec-a", "digest": "sha256:" + ("a" * 64)},
        "e-help": {"record_id": "rec-b", "digest": "sha256:" + ("b" * 64)},
    }
    events = [
        {"id": "e-success", "kind": "success", "pattern_key": PATTERN, "explained": False},
        {"id": "e-help", "kind": "help_request", "pattern_key": PATTERN},
    ]
    status, next_state, report = apply_ingest_confirmations(state, events, citations)
    assert status == "NO_CHANGE"
    assert _hypothesis(next_state)["confirmations"] == 0
    assert len(report["kinds_ignored"]) == 2


def test_human_correction_increments_once_per_distinct_record():
    state = {
        "signals": [],
        "observations": [],
        "hypotheses": [{
            "id": f"hypothesis:{PATTERN}",
            "type": "hypothesis",
            "status": "candidate",
            "pattern_key": PATTERN,
            "evidence_ids": ["o1", "o2"],
            "confirmations": 0,
            "explicit_human_validation": False,
        }],
        "rules": [],
        "principles": [],
    }
    digest_a = "sha256:" + ("a" * 64)
    digest_b = "sha256:" + ("b" * 64)
    citations = {
        "e1": {"record_id": "rec-a", "digest": digest_a},
        "e2": {"record_id": "rec-b", "digest": digest_b},
    }
    events = [
        {"id": "e1", "kind": "human_correction", "pattern_key": PATTERN},
        {"id": "e2", "kind": "human_correction", "pattern_key": PATTERN},
    ]
    status, next_state, report = apply_ingest_confirmations(state, events, citations)
    assert status == "CHANGE"
    assert _hypothesis(next_state)["confirmations"] == 1
    assert len(report["confirmations_applied"]) == 1
    assert report["confirmations_applied"][0]["record_id"] == "rec-a"


def test_replay_same_record_is_no_change(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(
        tmp_path / "bootstrap.json",
        intake_for(
            persisted,
            events=[
                {"id": "n1", "novel": True, "pattern_key": PATTERN},
                {"id": "c1", "kind": "human_correction", "pattern_key": PATTERN},
                {"id": "c2", "kind": "human_correction", "pattern_key": PATTERN},
            ],
        ),
    )
    first_code, first_env, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert first_code == 0 and first_env["status"] == "CHANGE"
    knowledge_before = (
        tmp_path / ".agent" / "knowledge" / "knowledge.json"
    ).read_bytes()

    replay_code, replay_env, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert replay_code == 0 and replay_env["status"] == "NO_CHANGE"
    assert (
        tmp_path / ".agent" / "knowledge" / "knowledge.json"
    ).read_bytes() == knowledge_before


def test_three_distinct_intakes_reach_three_confirmations_without_rules(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code1, env1, _ = _ingest_human_correction(
        tmp_path, capsys,
        record_id="session-one",
        event_id="hc-one-a",
        extra=[
            {"id": "novel-one", "novel": True, "pattern_key": PATTERN},
            {"id": "hc-one-b", "kind": "human_correction", "pattern_key": PATTERN},
        ],
    )
    assert code1 == 0 and env1["status"] == "CHANGE"
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    assert _hypothesis(knowledge)["confirmations"] == 1
    assert knowledge["rules"] == []

    code2, env2, _ = _ingest_human_correction(
        tmp_path, capsys, record_id="session-two", event_id="hc-two",
    )
    assert code2 == 0 and env2["status"] == "CHANGE"
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    assert _hypothesis(knowledge)["confirmations"] == 2

    code3, env3, _ = _ingest_human_correction(
        tmp_path, capsys, record_id="session-three", event_id="hc-three",
    )
    assert code3 == 0 and env3["status"] == "CHANGE"
    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    hypothesis = _hypothesis(knowledge)
    assert hypothesis["confirmations"] == 3
    assert hypothesis["explicit_human_validation"] is False
    assert knowledge["rules"] == []
    assert len(hypothesis.get("confirmation_source_records") or []) == 3


def test_envelope_announces_confirmations_applied(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code, envelope, _ = _ingest_human_correction(
        tmp_path, capsys,
        record_id="session-announce",
        event_id="hc-announce-a",
        extra=[
            {"id": "novel-announce", "novel": True, "pattern_key": PATTERN},
            {"id": "hc-announce-b", "kind": "human_correction", "pattern_key": PATTERN},
        ],
    )
    assert code == 0
    result = envelope["result"]
    assert "confirmations_applied" in result
    assert len(result["confirmations_applied"]) == 1
    assert result["confirmations_applied"][0]["kind"] == "human_correction"
    assert envelope["meta"]["confirmation_eligible_kinds"] == [
        "human_correction", "rule_mismatch",
    ]
    assert envelope["meta"]["confirmation_ignored_kinds"] == [
        "help_request", "success",
    ]


def test_success_events_are_listed_as_kinds_ignored(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(
        tmp_path / "intake-success.json",
        intake_for(
            persisted,
            events=[
                {"id": "n1", "novel": True, "pattern_key": PATTERN},
                {"id": "c1", "kind": "human_correction", "pattern_key": PATTERN},
                {"id": "c2", "kind": "human_correction", "pattern_key": PATTERN},
                {"id": "s1", "kind": "success", "pattern_key": PATTERN, "explained": False},
            ],
        ),
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert code == 0
    ignored = envelope["result"]["kinds_ignored"]
    assert any(item["kind"] == "success" and item["event_id"] == "s1" for item in ignored)
