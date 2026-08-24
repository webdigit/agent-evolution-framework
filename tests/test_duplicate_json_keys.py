"""Adversarial regression: duplicate JSON keys in governed command input.

On 55cc832, json.loads keeps the last occurrence silently. These tests assert
rejection with a distinct error code. They must fail on that baseline before the
Lot 1 fix lands.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from aef import cli
from aef.filesystem import apply_workspace, load_workspace
from aef.record_document import build_persisted_record


REGISTRY_PATH = ".agent/integrations/registry.json"
KNOWLEDGE_PATH = ".agent/knowledge/knowledge.json"


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def init_workspace(tmp_path, capsys):
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    )
    assert code == 0
    assert envelope["status"] in {"CHANGE", "NO_CHANGE"}
    return tmp_path


def recording_document(**overrides):
    document = {
        "protocol": "aef.record.submit/v1",
        "record_id": "session-alpha",
        "recorded_at": "2026-08-20T13:21:00Z",
        "declared_by": {"kind": "human", "identifier": "operator"},
        "payload": {
            "context": "reviewed a failed dry-run",
            "actions": [{"summary": "inspected the CLI envelope"}],
            "outcomes": [],
            "incidents": [],
            "evidence": [],
        },
    }
    document.update(overrides)
    return document


def persist_sample_record(tmp_path, capsys, document=None):
    path = tmp_path / "recording.json"
    path.write_text(json.dumps(document or recording_document()), encoding="utf-8")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(path),
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    return build_persisted_record(document or recording_document())


def assert_duplicate_key_rejected(code, envelope, key):
    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "duplicate_json_key"
    assert key in envelope["error"]["message"]
    assert envelope["error"].get("details", {}).get("key") == key


def test_ingest_rejects_duplicate_root_records_key(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    benign = {
        "record_id": persisted["record_id"],
        "digest": persisted["digest"],
        "events": [{"id": "E-benign", "novel": True, "pattern_key": "benign-pattern"}],
    }
    applied = {
        "record_id": persisted["record_id"],
        "digest": persisted["digest"],
        "events": [{"id": "E-hostile", "novel": True, "pattern_key": "hostile-pattern"}],
    }
    intake = tmp_path / "intake-dup.json"
    intake.write_text(
        "{"
        '"protocol":"aef.ingest.submit/v1",'
        f'"records":[{json.dumps(benign, separators=(",", ":"))}],'
        f'"records":[{json.dumps(applied, separators=(",", ":"))}]'
        "}",
        encoding="utf-8",
    )
    knowledge = tmp_path / KNOWLEDGE_PATH
    before = knowledge.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert_duplicate_key_rejected(code, envelope, "records")
    assert knowledge.read_bytes() == before
    text = knowledge.read_text(encoding="utf-8")
    assert "hostile-pattern" not in text
    assert "benign-pattern" not in text


def test_record_rejects_duplicate_root_record_id(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    recording = tmp_path / "recording-dup.json"
    recording.write_text(
        "{"
        '"protocol":"aef.record.submit/v1",'
        '"record_id":"visible-to-reviewer",'
        '"recorded_at":"2026-08-20T13:21:00Z",'
        '"declared_by":{"kind":"human","identifier":"operator"},'
        '"payload":{"context":"x","actions":[{"summary":"y"}],'
        '"outcomes":[],"incidents":[],"evidence":[]},'
        '"record_id":"second-occurrence-wins"'
        "}",
        encoding="utf-8",
    )

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )

    assert_duplicate_key_rejected(code, envelope, "record_id")
    records_dir = tmp_path / ".agent" / "records"
    assert not any(records_dir.rglob("*.json")) if records_dir.exists() else True


def test_discover_rejects_duplicate_root_connectors_key(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    first = [{
        "id": "harmless-connector",
        "status": "available",
        "capabilities": [{
            "id": "harmless.read",
            "operation": "read",
            "risk": "R0",
            "reversible": True,
            "available": True,
        }],
    }]
    second = [{
        "id": "hostile-connector",
        "status": "available",
        "capabilities": [{
            "id": "hostile.read",
            "operation": "read",
            "risk": "R0",
            "reversible": True,
            "available": True,
        }],
    }]
    snapshot = tmp_path / "snapshot-dup.json"
    snapshot.write_text(
        "{"
        f'"connectors":{json.dumps(first, separators=(",", ":"))},'
        f'"connectors":{json.dumps(second, separators=(",", ":"))}'
        "}",
        encoding="utf-8",
    )
    registry = tmp_path / REGISTRY_PATH
    before = registry.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "discover", "--snapshot", str(snapshot),
    )

    assert_duplicate_key_rejected(code, envelope, "connectors")
    assert registry.read_bytes() == before
    assert "hostile-connector" not in registry.read_text(encoding="utf-8")


def test_consolidate_rejects_duplicate_root_reviews_key(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    knowledge = {
        "signals": [],
        "observations": [
            {"id": "observation:one", "type": "observation", "status": "active"},
            {"id": "observation:two", "type": "observation", "status": "active"},
        ],
        "hypotheses": [],
        "rules": [{
            "id": "rule:verify-source",
            "type": "rule",
            "status": "active",
            "pattern_key": "verify-source",
            "evidence_ids": ["observation:one", "observation:two"],
        }],
        "principles": [],
        "mistakes": [],
    }
    current = load_workspace(tmp_path)
    desired = deepcopy(current)
    desired["files"][KNOWLEDGE_PATH] = knowledge
    apply_workspace(tmp_path, current, desired)
    keep = {
        "id": "review:keep:verify-source",
        "rule_id": "rule:verify-source",
        "action": "keep",
        "reason": "Leave the rule unchanged.",
        "evidence_ids": ["observation:one", "observation:two"],
    }
    specialize = {
        "id": "review:specialize:verify-source",
        "rule_id": "rule:verify-source",
        "action": "specialize",
        "reason": "Only ambiguous records need extra verification.",
        "evidence_ids": ["observation:one", "observation:two"],
        "context": {"record_type": "ambiguous"},
        "approval": {
            "approved": True,
            "source": "human",
            "actor": "Alex Example",
            "approved_at": "2026-08-14T14:00:00Z",
        },
    }
    reviews = tmp_path / "reviews-dup.json"
    reviews.write_text(
        "{"
        '"protocol":"aef.consolidate/v1",'
        f'"reviews":[{json.dumps(keep, separators=(",", ":"))}],'
        f'"reviews":[{json.dumps(specialize, separators=(",", ":"))}]'
        "}",
        encoding="utf-8",
    )
    target = tmp_path / KNOWLEDGE_PATH
    before = target.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "consolidate", "--reviews", str(reviews),
    )

    assert_duplicate_key_rejected(code, envelope, "reviews")
    assert target.read_bytes() == before


def test_evaluate_rejects_duplicate_root_decisions_key(tmp_path, capsys):
    pending = {
        "id": "promotion:career:global:L1:L2",
        "type": "promotion",
        "scope": "career",
        "competency_id": None,
        "from_level": "L1",
        "to_level": "L2",
        "status": "pending",
        "detected_at": "2026-08-14T10:00:00Z",
        "evidence": {
            "xp": 50,
            "cases": 10,
            "trust": 0.9,
            "complex_cases": 0,
            "recent_significant_errors": 0,
        },
        "evidence_digest": (
            "sha256:0040087530564ecf50925019a020cfe486ccf3c4c49d13fdd6d311432b443d92"
        ),
    }
    apply_workspace(
        tmp_path,
        load_workspace(tmp_path),
        {
            "files": {
                ".agent/manifest.json": {"framework": "aef"},
                ".agent/state/evaluations.json": {
                    "schema_version": "1.0.0",
                    "policy": {
                        "mode": "adaptive",
                        "every_tasks": None,
                        "interval_days": None,
                    },
                    "history": [],
                    "promotion_recommendations": [deepcopy(pending)],
                },
                ".agent/state/career.json": {
                    "level": "L1",
                    "xp": 50,
                    "cases": 10,
                    "trust": 0.9,
                    "complex_cases": 0,
                    "recent_significant_errors": 0,
                    "status": "active",
                    "probation": False,
                },
                ".agent/state/competencies.json": {},
            }
        },
    )
    approve = {
        "id": "evaluation:promotion:manual-001",
        "recommendation_id": pending["id"],
        "decision": "approve",
        "reason": "The current evidence supports promotion.",
        "expected_evidence_digest": pending["evidence_digest"],
        "expected_current_evidence_digest": pending["evidence_digest"],
        "approval": {
            "approved": True,
            "source": "human",
            "actor": "Alex Example",
            "approved_at": "2026-08-15T10:00:00Z",
        },
    }
    decisions = tmp_path / "decisions-dup.json"
    decisions.write_text(
        "{"
        '"protocol":"aef.evaluate/v1",'
        '"decisions":[],'
        f'"decisions":[{json.dumps(approve, separators=(",", ":"))}]'
        "}",
        encoding="utf-8",
    )
    career = tmp_path / ".agent" / "state" / "career.json"
    before = career.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "evaluate", "--decisions", str(decisions),
    )

    assert_duplicate_key_rejected(code, envelope, "decisions")
    assert career.read_bytes() == before
    assert json.loads(before)["level"] == "L1"


def test_competency_declare_rejects_duplicate_root_competency_id(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    citation = {
        "record_id": persisted["record_id"],
        "digest": persisted["digest"],
    }
    declaration = tmp_path / "declaration-dup.json"
    declaration.write_text(
        "{"
        '"protocol":"aef.competency.declare.submit/v1",'
        '"competency_id":"visible-to-reviewer",'
        '"title":"Dry-run review",'
        '"scope":"Inspect CLI dry-run outcomes",'
        '"limits":"No production mutation authority",'
        '"rationale":"Official birth after recorded review",'
        f'"records":[{json.dumps(citation, separators=(",", ":"))}],'
        '"decision":{"source":"human","actor":"operator",'
        '"decided_at":"2026-08-21T10:00:00Z","approved":true},'
        '"competency_id":"second-occurrence-wins"'
        "}",
        encoding="utf-8",
    )
    competencies = tmp_path / ".agent" / "state" / "competencies.json"
    before = competencies.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration),
    )

    assert_duplicate_key_rejected(code, envelope, "competency_id")
    assert competencies.read_bytes() == before
    text = competencies.read_text(encoding="utf-8")
    assert "second-occurrence-wins" not in text
    assert "visible-to-reviewer" not in text


def test_ingest_rejects_duplicate_key_inside_array_element(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = tmp_path / "intake-nested-dup.json"
    intake.write_text(
        "{"
        '"protocol":"aef.ingest.submit/v1",'
        '"records":[{'
        f'"record_id":{json.dumps(persisted["record_id"])},'
        f'"digest":{json.dumps(persisted["digest"])},'
        '"events":[{'
        '"id":"E1","novel":true,'
        '"pattern_key":"first-visible",'
        '"pattern_key":"second-applied"'
        "}]"
        "}]"
        "}",
        encoding="utf-8",
    )
    knowledge = tmp_path / KNOWLEDGE_PATH
    before = knowledge.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert_duplicate_key_rejected(code, envelope, "pattern_key")
    assert knowledge.read_bytes() == before


def test_evaluate_rejects_duplicate_key_inside_decision_object(tmp_path, capsys):
    pending = {
        "id": "promotion:career:global:L1:L2",
        "type": "promotion",
        "scope": "career",
        "competency_id": None,
        "from_level": "L1",
        "to_level": "L2",
        "status": "pending",
        "detected_at": "2026-08-14T10:00:00Z",
        "evidence": {
            "xp": 50,
            "cases": 10,
            "trust": 0.9,
            "complex_cases": 0,
            "recent_significant_errors": 0,
        },
        "evidence_digest": (
            "sha256:0040087530564ecf50925019a020cfe486ccf3c4c49d13fdd6d311432b443d92"
        ),
    }
    apply_workspace(
        tmp_path,
        load_workspace(tmp_path),
        {
            "files": {
                ".agent/manifest.json": {"framework": "aef"},
                ".agent/state/evaluations.json": {
                    "schema_version": "1.0.0",
                    "policy": {
                        "mode": "adaptive",
                        "every_tasks": None,
                        "interval_days": None,
                    },
                    "history": [],
                    "promotion_recommendations": [deepcopy(pending)],
                },
                ".agent/state/career.json": {
                    "level": "L1",
                    "xp": 50,
                    "cases": 10,
                    "trust": 0.9,
                    "complex_cases": 0,
                    "recent_significant_errors": 0,
                    "status": "active",
                    "probation": False,
                },
                ".agent/state/competencies.json": {},
            }
        },
    )
    decisions = tmp_path / "decision-object-dup.json"
    decisions.write_text(
        "{"
        '"protocol":"aef.evaluate/v1",'
        '"decisions":[{'
        '"id":"evaluation:promotion:manual-001",'
        f'"recommendation_id":{json.dumps(pending["id"])},'
        '"decision":"approve",'
        '"reason":"Visible first reason to the reviewer.",'
        f'"expected_evidence_digest":{json.dumps(pending["evidence_digest"])},'
        f'"expected_current_evidence_digest":{json.dumps(pending["evidence_digest"])},'
        '"approval":{"approved":true,"source":"human","actor":"Alex Example",'
        '"approved_at":"2026-08-15T10:00:00Z"},'
        '"reason":"Second occurrence is what would be applied."'
        "}]"
        "}",
        encoding="utf-8",
    )
    career = tmp_path / ".agent" / "state" / "career.json"
    before = career.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "evaluate", "--decisions", str(decisions),
    )

    assert_duplicate_key_rejected(code, envelope, "reason")
    assert career.read_bytes() == before


def test_load_snapshot_rejects_duplicate_key_in_nested_object(tmp_path):
    path = tmp_path / "nested.json"
    path.write_text(
        '{"outer":{"inner":1,"inner":2},"ok":true}',
        encoding="utf-8",
    )

    with pytest.raises(cli.CLIInputError) as raised:
        cli._load_snapshot(path)

    assert raised.value.code == "duplicate_json_key"
    assert raised.value.details["key"] == "inner"
    assert "inner" in raised.value.public_message
