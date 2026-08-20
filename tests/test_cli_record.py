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


def write_recording(path: Path, document=None):
    path.write_text(json.dumps(document or submission()), encoding="utf-8")
    return path


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured


def test_dry_run_change_does_not_create_records(tmp_path, capsys):
    recording = write_recording(tmp_path / "recording.json")

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording), "--dry-run",
    )

    expected = build_persisted_record(submission())
    assert code == 0
    assert envelope["command"] == "RECORD"
    assert envelope["status"] == "CHANGE"
    assert envelope["dry_run"] is True
    assert envelope["result"]["record_id"] == "session-alpha"
    assert envelope["result"]["path"] == ".agent/records/session-alpha.json"
    assert envelope["result"]["digest"] == expected["digest"]
    assert envelope["diff"] == {
        "created": [".agent/records/session-alpha.json"],
        "modified": [],
        "removed": [],
    }
    assert not (tmp_path / ".agent").exists()


def test_apply_creates_then_replay_is_no_change(tmp_path, capsys):
    recording = write_recording(tmp_path / "recording.json")

    first_code, first, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    second_code, second, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )

    assert first_code == second_code == 0
    assert first["status"] == "CHANGE"
    assert second["status"] == "NO_CHANGE"
    assert second["diff"] == {"created": [], "modified": [], "removed": []}
    assert (tmp_path / ".agent" / "records" / "session-alpha.json").is_file()


def test_conflict_exits_four_with_blocked_envelope(tmp_path, capsys):
    write_recording(tmp_path / "first.json", submission())
    cli.main([
        "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(tmp_path / "first.json"),
    ])
    capsys.readouterr()
    changed = submission()
    changed["payload"] = {**changed["payload"], "context": "a different fact"}
    write_recording(tmp_path / "second.json", changed)

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(tmp_path / "second.json"),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["ok"] is False
    assert envelope["error"] is None
    assert envelope["diff"] is None
    assert envelope["meta"]["reason"] == "record_conflict"
    stored = json.loads((tmp_path / ".agent" / "records" / "session-alpha.json").read_text(encoding="utf-8"))
    assert stored["payload"]["context"] == "reviewed a failed dry-run"


def test_persisted_document_as_input_exits_three(tmp_path, capsys):
    persisted = build_persisted_record(submission())
    write_recording(tmp_path / "persisted.json", persisted)

    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(tmp_path / "persisted.json"),
    )

    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["error"]["code"] == "invalid_record_submission"
    assert not (tmp_path / ".agent" / "records").exists()


def test_invalid_record_id_exits_three(tmp_path, capsys):
    write_recording(tmp_path / "bad.json", submission(record_id="Session-alpha"))

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(tmp_path / "bad.json"), "--dry-run",
    )

    assert code == 3
    assert envelope["error"]["code"] == "invalid_record_id"
    assert not (tmp_path / ".agent" / "records").exists()


def test_invalid_recording_json_uses_stable_public_message(tmp_path, capsys):
    recording = tmp_path / "recording.json"
    recording.write_text("{broken", encoding="utf-8")

    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording), "--dry-run",
    )

    assert code == 3
    assert envelope["status"] == "ERROR"
    assert envelope["error"] == {
        "code": "invalid_json",
        "message": "The recording document is not valid JSON.",
        "details": {},
    }
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "{broken" not in captured.out
    assert "{broken" not in captured.err
    assert not (tmp_path / ".agent").exists()


def test_invalid_recording_json_human_renderer_is_stable(tmp_path, capsys):
    recording = tmp_path / "recording.json"
    recording.write_text("{broken", encoding="utf-8")

    code = cli.main([
        "--human", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    ])
    captured = capsys.readouterr()

    assert code == 3
    assert captured.out.startswith("[ERROR] The recording document is not valid JSON.\n")
    assert "Code      : invalid_json" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "{broken" not in captured.out
    assert "{broken" not in captured.err
    assert not (tmp_path / ".agent").exists()


def test_tampered_existing_record_is_blocked_without_rewrite(tmp_path, capsys):
    recording = write_recording(tmp_path / "recording.json")
    cli.main([
        "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    ])
    capsys.readouterr()
    path = tmp_path / ".agent" / "records" / "session-alpha.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["payload"]["context"] = "tampered after persist"
    path.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = path.read_bytes()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "record_conflict"
    assert path.read_bytes() == before


def test_human_renderer_accepts_record(tmp_path, capsys):
    recording = write_recording(tmp_path / "recording.json")

    code = cli.main([
        "--human", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    ])
    output = capsys.readouterr().out

    assert code == 0
    assert output.startswith("[OK] AEF recorded a declaration\n")
    assert "Record    : session-alpha" in output
