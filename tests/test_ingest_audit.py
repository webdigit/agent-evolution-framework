from __future__ import annotations

import json
from copy import deepcopy

from aef import cli
from aef.filesystem import apply_workspace, load_workspace
from aef.operations import audit_project
from tests.test_cli_ingest import (
    intake_for,
    init_workspace,
    invoke,
    persist_sample_record,
    write_json,
)


def test_workspace_without_ingest_stays_pass(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persist_sample_record(tmp_path, capsys)

    code, envelope, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "audit")

    assert code == 0
    assert envelope["status"] == "PASS"
    ids = {finding["id"] for finding in envelope["result"]["findings"]}
    assert "knowledge-missing-provenance" not in ids
    assert "ingest-record-missing" not in ids
    assert "ingest-record-digest-mismatch" not in ids


def test_orphan_knowledge_is_an_ingest_finding(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    current = load_workspace(tmp_path)
    desired = deepcopy(current)
    knowledge = deepcopy(desired["files"][".agent/knowledge/knowledge.json"])
    knowledge["signals"] = [{
        "id": "signal:novelty:orphan",
        "type": "novelty",
        "status": "candidate",
    }]
    desired["files"][".agent/knowledge/knowledge.json"] = knowledge
    apply_workspace(tmp_path, current, desired)

    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    ids = {finding["id"] for finding in result["findings"]}
    assert result["status"] == "FAIL"
    assert "knowledge-missing-provenance" in ids


def test_cited_missing_record_is_ingest_finding(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    current = load_workspace(tmp_path)
    desired = deepcopy(current)
    knowledge = deepcopy(desired["files"][".agent/knowledge/knowledge.json"])
    knowledge["signals"] = [{
        "id": "signal:novelty:gap",
        "type": "novelty",
        "status": "candidate",
        "source_records": [{
            "record_id": "missing-record",
            "digest": "sha256:" + ("a" * 64),
        }],
    }]
    desired["files"][".agent/knowledge/knowledge.json"] = knowledge
    apply_workspace(tmp_path, current, desired)

    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    ids = {finding["id"] for finding in result["findings"]}
    assert "ingest-record-missing" in ids
    assert "record-unexpected-entry" not in ids


def test_digest_mismatch_on_valid_record_is_ingest_finding(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persist_sample_record(tmp_path, capsys)
    current = load_workspace(tmp_path)
    desired = deepcopy(current)
    knowledge = deepcopy(desired["files"][".agent/knowledge/knowledge.json"])
    knowledge["signals"] = [{
        "id": "signal:novelty:gap",
        "type": "novelty",
        "status": "candidate",
        "source_records": [{
            "record_id": "session-alpha",
            "digest": "sha256:" + ("b" * 64),
        }],
    }]
    desired["files"][".agent/knowledge/knowledge.json"] = knowledge
    apply_workspace(tmp_path, current, desired)

    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    ids = {finding["id"] for finding in result["findings"]}
    assert "ingest-record-digest-mismatch" in ids


def test_replay_after_audit_pass_is_no_change(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    apply_code, _, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert apply_code == 0
    audit_code, audit_envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "audit",
    )
    assert audit_code == 0
    assert audit_envelope["status"] == "PASS"
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()

    replay_code, replay, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert replay_code == 0
    assert replay["status"] == "NO_CHANGE"
    assert knowledge.read_bytes() == before
    human_code = cli.main(["--human", "--workspace", str(tmp_path), "audit"])
    human_out = capsys.readouterr().out
    assert human_code == 0
    assert "FAIL" not in human_out
    dumped = json.dumps(audit_envelope)
    assert "knowledge-missing-provenance" not in dumped
