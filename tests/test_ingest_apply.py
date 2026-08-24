from __future__ import annotations

import json
from pathlib import Path

from aef import cli
from aef.record_document import build_persisted_record
from tests.test_cli_ingest import (
    intake_for,
    init_workspace,
    invoke,
    persist_sample_record,
    submission,
    write_json,
)


def snapshot_state(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / ".agent").rglob("*")
        if path.is_file()
    }


def test_apply_writes_knowledge_via_ingest_events(tmp_path, capsys, monkeypatch):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    called = {"count": 0}
    original = __import__("aef.ingest_ops", fromlist=["ingest_events"]).ingest_events

    def wrapped(state, events):
        called["count"] += 1
        return original(state, events)

    monkeypatch.setattr("aef.ingest_ops.ingest_events", wrapped)

    records_before = (tmp_path / ".agent" / "records" / "session-alpha.json").read_bytes()
    career_before = (tmp_path / ".agent" / "state" / "career.json").read_bytes()
    competencies_before = (tmp_path / ".agent" / "state" / "competencies.json").read_bytes()
    evaluations_before = (tmp_path / ".agent" / "state" / "evaluations.json").read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert called["count"] == 1
    knowledge = json.loads(
        (tmp_path / ".agent" / "knowledge" / "knowledge.json").read_text(encoding="utf-8")
    )
    assert knowledge["signals"][0]["id"] == "signal:novelty:init-dry-run"
    assert knowledge["signals"][0]["source_records"][0]["record_id"] == "session-alpha"
    assert knowledge["rules"] == []
    assert knowledge["principles"] == []
    assert (tmp_path / ".agent" / "records" / "session-alpha.json").read_bytes() == records_before
    assert (tmp_path / ".agent" / "state" / "career.json").read_bytes() == career_before
    assert (tmp_path / ".agent" / "state" / "competencies.json").read_bytes() == competencies_before
    assert (tmp_path / ".agent" / "state" / "evaluations.json").read_bytes() == evaluations_before


def test_replay_is_no_change_without_rewrite(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    first, _, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert first == 0
    before = snapshot_state(tmp_path)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 0
    assert envelope["status"] == "NO_CHANGE"
    assert snapshot_state(tmp_path) == before


def test_evaluate_transaction_blocks_before_write(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    transaction = tmp_path / ".agent" / "state" / "evaluation-transaction.json"
    transaction.write_text("{}", encoding="utf-8")
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "evaluation_recovery_required"
    assert knowledge.read_bytes() == before


def test_record_document_is_not_used_as_learn_flag(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persist_sample_record(tmp_path, capsys)
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()
    recording = write_json(tmp_path / "again.json", submission(record_id="session-beta"))
    code, _, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    assert code == 0
    assert knowledge.read_bytes() == before
    assert build_persisted_record(submission(record_id="session-beta"))["record_id"] == "session-beta"
