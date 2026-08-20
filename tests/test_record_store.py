import json
from pathlib import Path

import pytest

from aef.filesystem import JSON_PATHS, RECORDS_DIRECTORY
from aef.record_document import build_persisted_record
from aef.record_store import InvalidRecordStoreError, persist_record


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


def test_json_paths_does_not_gain_a_records_singleton():
    assert RECORDS_DIRECTORY == ".agent/records"
    assert RECORDS_DIRECTORY not in JSON_PATHS
    assert ".agent/records.json" not in JSON_PATHS


def test_first_apply_creates_records_directory_and_file(tmp_path: Path):
    persisted = build_persisted_record(submission())

    status, relative, digest = persist_record(tmp_path, persisted)

    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    assert status == "CHANGE"
    assert relative == ".agent/records/session-alpha.json"
    assert digest == persisted["digest"]
    assert path.is_file()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["digest"] == persisted["digest"]
    assert stored["protocol"] == "aef.record/v1"


def test_dry_run_change_does_not_create_records_directory(tmp_path: Path):
    persisted = build_persisted_record(submission())

    status, relative, digest = persist_record(tmp_path, persisted, dry_run=True)

    assert status == "CHANGE"
    assert relative == ".agent/records/session-alpha.json"
    assert digest == persisted["digest"]
    assert not (tmp_path / ".agent" / "records").exists()


def test_replay_same_digest_is_no_change_without_rewrite(tmp_path: Path):
    persisted = build_persisted_record(submission())
    persist_record(tmp_path, persisted)
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    before = path.read_bytes()

    status, _, digest = persist_record(tmp_path, persisted)

    assert status == "NO_CHANGE"
    assert digest == persisted["digest"]
    assert path.read_bytes() == before


def test_same_id_different_body_is_blocked_without_rewrite(tmp_path: Path):
    first = build_persisted_record(submission())
    persist_record(tmp_path, first)
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    before = path.read_bytes()
    changed = submission()
    changed["payload"] = {
        **changed["payload"],
        "context": "a different declared fact",
    }
    second = build_persisted_record(changed)

    status, _, _ = persist_record(tmp_path, second)

    assert status == "BLOCKED"
    assert path.read_bytes() == before


def test_dry_run_conflict_does_not_write(tmp_path: Path):
    persist_record(tmp_path, build_persisted_record(submission()))
    changed = submission()
    changed["payload"] = {**changed["payload"], "context": "conflict"}
    second = build_persisted_record(changed)

    status, _, _ = persist_record(tmp_path, second, dry_run=True)

    assert status == "BLOCKED"
    stored = json.loads((tmp_path / ".agent" / "records" / "session-alpha.json").read_text(encoding="utf-8"))
    assert stored["payload"]["context"] == "reviewed a failed dry-run"


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_symlink_target_is_refused(tmp_path: Path):
    records = tmp_path / ".agent" / "records"
    records.mkdir(parents=True)
    real = tmp_path / "outside.json"
    real.write_text("{}", encoding="utf-8")
    link = records / "session-alpha.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation requires privilege")

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, build_persisted_record(submission()))
    assert raised.value.code == "record_target_unsafe"
