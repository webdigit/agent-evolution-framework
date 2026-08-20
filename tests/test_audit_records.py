import json
from pathlib import Path

import pytest

from aef import cli
from aef.operations import audit_project, init_project
from aef.record_audit import _has_case_collision, audit_records_directory
from aef.record_document import build_persisted_record
from aef.record_store import persist_record
from aef.filesystem import apply_workspace, load_workspace


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


def initialized(tmp_path: Path):
    current = load_workspace(tmp_path)
    status, desired, _ = init_project(
        current, instance_id="agent-1",
        answers={"decision.role.primary.v1": "operator"},
        created_at="2026-08-20T13:21:00Z",
        profile="aef-v1",
    )
    assert status == "CHANGE"
    apply_workspace(tmp_path, current, desired)
    return tmp_path


def test_missing_and_empty_records_are_pass(tmp_path: Path):
    initialized(tmp_path)
    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    assert result["status"] == "PASS"
    assert not any(item["id"].startswith("record") for item in result["findings"])

    (tmp_path / ".agent" / "records").mkdir()
    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    assert result["status"] == "PASS"


def test_valid_record_keeps_audit_pass(tmp_path: Path):
    initialized(tmp_path)
    persist_record(tmp_path, build_persisted_record(submission()))
    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    assert result["status"] == "PASS"
    assert not any(item["severity"] == "error" and item["id"].startswith("record") for item in result["findings"])


def test_wrong_digest_fails_audit(tmp_path: Path):
    initialized(tmp_path)
    persist_record(tmp_path, build_persisted_record(submission()))
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["digest"] = "sha256:" + ("0" * 64)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["id"] == "record-digest-mismatch" for item in result["findings"])


def test_divergent_filename_fails_audit(tmp_path: Path):
    initialized(tmp_path)
    persist_record(tmp_path, build_persisted_record(submission()))
    source = tmp_path / ".agent" / "records" / "session-alpha.json"
    source.rename(tmp_path / ".agent" / "records" / "session-beta.json")

    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["id"] == "record-id-path-mismatch" for item in result["findings"])


def test_foreign_extension_and_subdirectory_fail(tmp_path: Path):
    initialized(tmp_path)
    records = tmp_path / ".agent" / "records"
    records.mkdir()
    (records / "notes.txt").write_text("nope", encoding="utf-8")
    (records / "nested").mkdir()

    findings = {item["id"] for item in audit_records_directory(tmp_path)}
    assert "record-unexpected-entry" in findings


def test_case_collision_detector():
    assert _has_case_collision(["session-alpha.json", "Session-alpha.json"])
    assert not _has_case_collision(["session-alpha.json", "session-beta.json"])


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_symlink_records_directory_fails(tmp_path: Path):
    initialized(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / ".agent" / "records"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires privilege")

    result = audit_project(load_workspace(tmp_path), root=tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["id"] == "record-symlink" for item in result["findings"])


def test_cli_audit_pass_without_records(tmp_path, capsys):
    initialized(tmp_path)
    code = cli.main(["--json", "--workspace", str(tmp_path), "audit"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert envelope["status"] == "PASS"
