from __future__ import annotations

import json
from pathlib import Path

from aef import cli
from aef.record_document import build_persisted_record


def submission(**overrides):
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


def intake_for(persisted, events=None):
    return {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": persisted["record_id"],
            "digest": persisted["digest"],
            "events": events or [
                {"id": "E1", "novel": True, "pattern_key": "init-dry-run"},
            ],
        }],
    }


def write_json(path: Path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


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


def persist_sample_record(tmp_path, capsys, document=None):
    recording = write_json(tmp_path / "recording.json", document or submission())
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    return build_persisted_record(document or submission())


def test_dry_run_change_does_not_write_knowledge(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake), "--dry-run",
    )

    assert code == 0
    assert envelope["command"] == "INGEST"
    assert envelope["status"] == "CHANGE"
    assert envelope["dry_run"] is True
    assert envelope["result"]["projected"]["signals"] == ["signal:novelty:init-dry-run"]
    assert envelope["result"]["human_action_required"] is False
    assert knowledge.read_bytes() == before


def test_human_and_json_share_the_same_decision(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))

    json_code, json_envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake), "--dry-run",
    )
    human_code = cli.main([
        "--human", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake), "--dry-run",
    ])
    human_out = capsys.readouterr().out

    assert json_code == human_code == 0
    assert json_envelope["status"] == "CHANGE"
    assert "[OK]" in human_out
    assert "ingest plan is ready" in human_out


def test_invalid_intake_exits_three(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", {"protocol": "nope"})

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake), "--dry-run",
    )

    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["error"]["code"] == "invalid_ingest_submission"
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    assert "signal:novelty" not in knowledge.read_text(encoding="utf-8")


def test_missing_record_exits_four_without_write(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = build_persisted_record(submission())
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "record_missing"
    assert knowledge.read_bytes() == before


def test_digest_mismatch_exits_four_without_write(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    wrong = dict(persisted)
    wrong["digest"] = "sha256:" + ("b" * 64)
    intake = write_json(tmp_path / "intake.json", intake_for(wrong))
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "record_digest_mismatch"
    assert knowledge.read_bytes() == before


def test_uninitialized_workspace_is_blocked_without_write(tmp_path, capsys):
    persisted = build_persisted_record(submission())
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "workspace_not_initialized"
    assert not (tmp_path / ".agent").exists()


def test_record_without_ingest_does_not_mutate_knowledge(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()
    persist_sample_record(tmp_path, capsys)
    assert knowledge.read_bytes() == before
