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


def declaration_for(persisted, **overrides):
    document = {
        "protocol": "aef.competency.declare.submit/v1",
        "competency_id": "dry-run-review",
        "title": "Dry-run review",
        "scope": "Inspect CLI dry-run outcomes",
        "limits": "No production mutation authority",
        "rationale": "Official birth after recorded review",
        "records": [{
            "record_id": persisted["record_id"],
            "digest": persisted["digest"],
        }],
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    }
    document.update(overrides)
    return document


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


def test_dry_run_change_does_not_write_competencies(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    declaration = write_json(
        tmp_path / "declaration.json", declaration_for(persisted),
    )
    competencies = tmp_path / ".agent" / "state" / "competencies.json"
    before = competencies.read_bytes()
    ledger = tmp_path / ".agent" / "state" / "competency-declarations.json"
    assert not ledger.exists()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration), "--dry-run",
    )

    assert code == 0
    assert envelope["command"] == "COMPETENCY_DECLARE"
    assert envelope["status"] == "CHANGE"
    assert envelope["dry_run"] is True
    assert envelope["result"]["competency_id"] == "dry-run-review"
    assert envelope["result"]["projected"]["level"] == "L1"
    assert envelope["result"]["human_action_required"] is False
    assert competencies.read_bytes() == before
    assert not ledger.exists()


def test_human_and_json_share_the_same_decision(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    declaration = write_json(
        tmp_path / "declaration.json", declaration_for(persisted),
    )

    code_json, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration), "--dry-run",
    )
    code_human, _, captured = invoke(
        capsys, "--human", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration), "--dry-run",
    )

    assert code_json == 0
    assert code_human == 0
    assert envelope["status"] == "CHANGE"
    assert "dry-run-review" in captured.out
    assert "[OK]" in captured.out


def test_missing_record_is_blocked_without_write(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    fake = {
        "record_id": "missing-record",
        "digest": "sha256:" + ("c" * 64),
    }
    declaration = write_json(
        tmp_path / "declaration.json",
        declaration_for(fake, records=[{
            "record_id": "missing-record",
            "digest": fake["digest"],
        }]),
    )
    competencies = tmp_path / ".agent" / "state" / "competencies.json"
    before = competencies.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration), "--dry-run",
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "record_missing"
    assert competencies.read_bytes() == before


def test_invalid_declaration_is_error(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    declaration = write_json(tmp_path / "declaration.json", {"protocol": "nope"})
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration), "--dry-run",
    )
    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["command"] == "COMPETENCY_DECLARE"


def test_apply_change_and_replay_no_change(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    declaration = write_json(
        tmp_path / "declaration.json", declaration_for(persisted),
    )
    career = tmp_path / ".agent" / "state" / "career.json"
    evaluations = tmp_path / ".agent" / "state" / "evaluations.json"
    record_path = tmp_path / ".agent" / "records" / "session-alpha.json"
    career_before = career.read_bytes()
    evaluations_before = evaluations.read_bytes()
    record_before = record_path.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration),
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    competencies = json.loads(
        (tmp_path / ".agent" / "state" / "competencies.json").read_text(encoding="utf-8")
    )
    assert competencies["dry-run-review"]["level"] == "L1"
    assert competencies["dry-run-review"]["xp"] == 0
    assert competencies["dry-run-review"]["trust"] is None
    ledger = json.loads(
        (tmp_path / ".agent" / "state" / "competency-declarations.json").read_text(
            encoding="utf-8"
        )
    )
    assert ledger["protocol"] == "aef.competency-declarations/v1"
    assert ledger["events"][0]["competency_id"] == "dry-run-review"
    assert career.read_bytes() == career_before
    assert evaluations.read_bytes() == evaluations_before
    assert record_path.read_bytes() == record_before

    code2, envelope2, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration),
    )
    assert code2 == 0
    assert envelope2["status"] == "NO_CHANGE"


def test_evaluate_record_ingest_do_not_create_competency(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    competencies = tmp_path / ".agent" / "state" / "competencies.json"
    before = competencies.read_bytes()

    invoke(capsys, "--json", "--workspace", str(tmp_path), "evaluate", "--list")
    intake = write_json(tmp_path / "intake.json", {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": persisted["record_id"],
            "digest": persisted["digest"],
            "events": [{"id": "e1", "novel": True, "pattern_key": "init-dry-run"}],
        }],
    })
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert competencies.read_bytes() == before


def test_audit_brownfield_warning_and_empty_init(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code, envelope, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "audit")
    assert code == 0
    assert envelope["status"] == "PASS"
    assert not any(
        item["id"] == "competency-missing-declaration-provenance"
        for item in envelope["result"]["findings"]
    )

    competencies = tmp_path / ".agent" / "state" / "competencies.json"
    competencies.write_text(
        json.dumps({
            "manual-skill": {
                "id": "manual-skill",
                "title": "Manual",
                "level": "L1",
                "xp": 0,
                "trust": None,
            }
        }),
        encoding="utf-8",
    )
    code2, envelope2, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "audit")
    assert code2 == 0
    assert envelope2["status"] == "PASS"
    assert not any(
        item["id"] == "competency-missing-declaration-provenance"
        for item in envelope2["result"]["findings"]
    )


def test_governed_birth_has_no_brownfield_warning(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    declaration = write_json(
        tmp_path / "declaration.json", declaration_for(persisted),
    )
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--declaration", str(declaration),
    )
    code, envelope, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "audit")
    assert code == 0
    assert envelope["status"] == "PASS"
    assert not any(
        item["id"] == "competency-missing-declaration-provenance"
        for item in envelope["result"]["findings"]
    )
