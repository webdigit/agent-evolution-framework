from __future__ import annotations

import json
from pathlib import Path

from aef import cli
from aef.competency_declaration_transaction import (
    TRANSACTION_PATH,
    build_declaration_transaction,
    filesystem_json,
)
from aef.filesystem import _apply_workspace_unchecked, load_workspace
from aef.record_document import build_persisted_record


def submission():
    return {
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


def declaration_for(persisted):
    return {
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


def write_json(path: Path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def init_and_record(tmp_path, capsys):
    invoke(
        capsys, "--json", "--workspace", str(tmp_path), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    )
    recording = write_json(tmp_path / "recording.json", submission())
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    return build_persisted_record(submission())


def test_recover_prepared_journal_rollback(tmp_path, capsys):
    persisted = init_and_record(tmp_path, capsys)
    current = load_workspace(tmp_path)
    desired = json.loads(json.dumps(current))
    desired["files"][".agent/state/competencies.json"] = {
        "dry-run-review": {
            "id": "dry-run-review",
            "title": "Dry-run review",
            "level": "L1",
            "xp": 0,
            "cases": 0,
            "trust": None,
            "complex_cases": 0,
            "recent_significant_errors": 0,
            "probation": False,
            "source": "declared",
        }
    }
    desired["files"][".agent/state/competency-declarations.json"] = {
        "protocol": "aef.competency-declarations/v1",
        "events": [{
            "event_id": "competency-declaration:deadbeef",
            "competency_id": "dry-run-review",
            "declared_at": "2026-08-21T10:00:00Z",
            "decision": declaration_for(persisted)["decision"],
            "records": declaration_for(persisted)["records"],
            "title": "Dry-run review",
            "scope": "s",
            "limits": "l",
            "rationale": "r",
            "declaration_digest": "sha256:" + ("d" * 64),
        }],
    }
    journal = build_declaration_transaction(
        current, desired, "sha256:" + ("d" * 64),
    )
    prepared = json.loads(json.dumps(current))
    prepared["files"][TRANSACTION_PATH] = journal
    _apply_workspace_unchecked(tmp_path, current, prepared, allow_delete=False)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "competency", "declare", "--recover",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert envelope["result"]["recovery_action"] == "rollback"
    assert not (tmp_path / ".agent" / "state" / "competency-declaration-transaction.json").exists()
    competencies = json.loads(
        (tmp_path / ".agent" / "state" / "competencies.json").read_text(encoding="utf-8")
    )
    assert competencies == {}


def test_open_declaration_journal_blocks_ingest(tmp_path, capsys):
    persisted = init_and_record(tmp_path, capsys)
    current = load_workspace(tmp_path)
    desired = json.loads(json.dumps(current))
    desired["files"][".agent/state/competencies.json"] = {
        "dry-run-review": {
            "id": "dry-run-review",
            "title": "Dry-run review",
            "level": "L1",
            "xp": 0,
            "cases": 0,
            "trust": None,
            "complex_cases": 0,
            "recent_significant_errors": 0,
            "probation": False,
            "source": "declared",
        }
    }
    desired["files"][".agent/state/competency-declarations.json"] = {
        "protocol": "aef.competency-declarations/v1",
        "events": [],
    }
    journal = build_declaration_transaction(
        current, desired, "sha256:" + ("e" * 64),
    )
    prepared = json.loads(json.dumps(current))
    prepared["files"][TRANSACTION_PATH] = journal
    _apply_workspace_unchecked(tmp_path, current, prepared, allow_delete=False)

    intake = write_json(tmp_path / "intake.json", {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": persisted["record_id"],
            "digest": persisted["digest"],
            "events": [{"id": "e1", "novel": True, "pattern_key": "x"}],
        }],
    })
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake), "--dry-run",
    )
    assert code == 4
    assert envelope["meta"]["reason"] == "competency_declaration_recovery_required"

    code_audit, envelope_audit, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "audit",
    )
    assert code_audit == 1
    assert envelope_audit["status"] == "FAIL"
    assert any(
        item["id"] == "competency-declaration-recovery-required"
        and item["severity"] == "error"
        for item in envelope_audit["result"]["findings"]
    )


def test_filesystem_json_helper_stable():
    assert filesystem_json({"a": 1}).endswith("\n")
