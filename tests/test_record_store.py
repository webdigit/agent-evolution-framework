import json
import os
import subprocess
import sys
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
    assert not (tmp_path / ".agent").exists()


def test_replay_same_digest_is_no_change_without_rewrite(tmp_path: Path):
    persisted = build_persisted_record(submission())
    persist_record(tmp_path, persisted)
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    before = path.read_bytes()

    status, _, digest = persist_record(tmp_path, persisted)

    assert status == "NO_CHANGE"
    assert digest == persisted["digest"]
    assert path.read_bytes() == before

    dry_status, _, _ = persist_record(tmp_path, persisted, dry_run=True)
    assert dry_status == "NO_CHANGE"
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


def test_existing_body_tampered_with_old_digest_is_blocked(tmp_path: Path):
    persisted = build_persisted_record(submission())
    persist_record(tmp_path, persisted)
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["payload"]["context"] = "tampered after persist"
    path.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tampered = path.read_bytes()

    status, _, _ = persist_record(tmp_path, persisted)

    assert status == "BLOCKED"
    assert path.read_bytes() == tampered


def test_existing_digest_tampered_is_blocked(tmp_path: Path):
    persisted = build_persisted_record(submission())
    persist_record(tmp_path, persisted)
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["digest"] = "sha256:" + ("0" * 64)
    path.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tampered = path.read_bytes()

    status, _, _ = persist_record(tmp_path, persisted)

    assert status == "BLOCKED"
    assert path.read_bytes() == tampered


def test_existing_invalid_json_is_blocked(tmp_path: Path):
    persisted = build_persisted_record(submission())
    persist_record(tmp_path, persisted)
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    path.write_text("{broken", encoding="utf-8")
    before = path.read_bytes()

    status, _, _ = persist_record(tmp_path, persisted)

    assert status == "BLOCKED"
    assert path.read_bytes() == before


def test_records_directory_reparse_point_is_refused(tmp_path: Path, monkeypatch):
    records = tmp_path / ".agent" / "records"
    records.mkdir(parents=True)
    monkeypatch.setattr(
        "aef.record_store.is_link_or_reparse_point",
        lambda path: path == records,
    )

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, build_persisted_record(submission()))
    assert raised.value.code == "record_target_unsafe"
    assert list(records.iterdir()) == []


def test_record_file_reparse_point_is_refused(tmp_path: Path, monkeypatch):
    records = tmp_path / ".agent" / "records"
    records.mkdir(parents=True)
    target = records / "session-alpha.json"
    target.write_text("{}", encoding="utf-8")
    before = target.read_bytes()
    monkeypatch.setattr(
        "aef.record_store.is_link_or_reparse_point",
        lambda path: path == target,
    )

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, build_persisted_record(submission()))
    assert raised.value.code == "record_target_unsafe"
    assert target.read_bytes() == before


def _try_file_symlink(link: Path, target: Path) -> bool:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


def test_symlink_target_is_refused_when_available(tmp_path: Path):
    records = tmp_path / ".agent" / "records"
    records.mkdir(parents=True)
    real = tmp_path / "outside.json"
    real.write_text("{}", encoding="utf-8")
    link = records / "session-alpha.json"
    if not _try_file_symlink(link, real):
        pytest.skip("symlink creation requires privilege")

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, build_persisted_record(submission()))
    assert raised.value.code == "record_target_unsafe"
    assert real.read_text(encoding="utf-8") == "{}"


@pytest.mark.skipif(sys.platform == "win32", reason="live POSIX symlink coverage for Linux CI")
def test_posix_symlink_file_and_directory_are_refused(tmp_path: Path):
    persisted = build_persisted_record(submission())
    records = tmp_path / ".agent" / "records"
    records.mkdir(parents=True)
    real_file = tmp_path / "outside.json"
    real_file.write_text("{}", encoding="utf-8")
    file_link = records / "session-alpha.json"
    file_link.symlink_to(real_file)

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, persisted)
    assert raised.value.code == "record_target_unsafe"
    assert real_file.read_text(encoding="utf-8") == "{}"

    file_link.unlink()
    records.rmdir()
    real_dir = tmp_path / "outside-records"
    real_dir.mkdir()
    records.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, persisted)
    assert raised.value.code == "record_target_unsafe"
    assert list(real_dir.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction protection")
def test_windows_junction_records_directory_is_refused(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    agent = tmp_path / ".agent"
    agent.mkdir()
    junction = agent / "records"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junctions are unavailable: {result.stderr or result.stdout}")

    with pytest.raises(InvalidRecordStoreError) as raised:
        persist_record(tmp_path, build_persisted_record(submission()))
    assert raised.value.code == "record_target_unsafe"
    assert list(outside.iterdir()) == []
