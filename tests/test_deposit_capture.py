from __future__ import annotations

import json
from pathlib import Path

import pytest

from aef.deposit_capture import InvalidDepositFilenameError, write_deposit_envelope
from aef.deposit_intake import (
    DEPOSIT_PROTOCOL,
    InvalidDepositSubmissionError,
    validate_deposit_submission,
)
from aef.identifiers import COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE
from aef.workspace_resolution import resolve_cli_workspace, resolve_deposit_dir
from tests.test_cli_ingest import init_workspace


def deposit_envelope(**overrides):
    document = {
        "protocol": DEPOSIT_PROTOCOL,
        "record_id": "remote-signal",
        "recorded_at": "2026-08-20T13:21:00Z",
        "declared_by": {"kind": "agent", "identifier": "remote-agent"},
        "payload": {
            "context": "Observed a failed dry-run from a remote host.",
            "actions": [{"summary": "Captured the operator-visible outcome"}],
            "outcomes": [],
            "incidents": [],
            "evidence": [],
        },
        "events": [
            {"id": "e1", "novel": True, "pattern_key": "init-dry-run"},
        ],
    }
    document.update(overrides)
    return document


def _engine_snapshot(root: Path) -> dict[str, bytes | tuple[str, ...]]:
    career = root / ".agent" / "state" / "career.json"
    knowledge = root / ".agent" / "knowledge" / "knowledge.json"
    competencies = root / ".agent" / "state" / "competencies.json"
    records_dir = root / ".agent" / "records"
    return {
        "career": career.read_bytes() if career.is_file() else b"",
        "knowledge": knowledge.read_bytes() if knowledge.is_file() else b"",
        "competencies": competencies.read_bytes() if competencies.is_file() else b"",
        "records": tuple(sorted(path.name for path in records_dir.glob("*.json")))
        if records_dir.is_dir()
        else (),
    }


def test_validate_deposit_submission_rejects_digest_and_colon():
    with pytest.raises(InvalidDepositSubmissionError) as raised:
        validate_deposit_submission(
            deposit_envelope(digest="sha256:" + ("a" * 64)),
        )
    assert raised.value.code == "invalid_deposit_submission"

    with pytest.raises(InvalidDepositSubmissionError) as raised:
        validate_deposit_submission(deposit_envelope(record_id="bad:id"))
    assert raised.value.code == "invalid_record_id"
    assert str(raised.value) == COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE


def test_resolve_deposit_dir_uses_resolved_workspace_not_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "Vincent"
    workspace.mkdir()
    (workspace / ".agent").mkdir()
    nested = workspace / "_upgrade"
    nested.mkdir()
    monkeypatch.chdir(nested)
    resolution = resolve_cli_workspace(None)
    assert resolution.walked_up is True
    assert resolve_deposit_dir(resolution) == workspace / ".aef-deposit"


def test_writing_deposit_envelope_does_not_modify_career_knowledge_records_or_competencies(
    tmp_path,
    capsys,
):
    init_workspace(tmp_path, capsys)
    before = _engine_snapshot(tmp_path)
    resolution = resolve_cli_workspace(str(tmp_path))
    envelope = deposit_envelope()
    target = write_deposit_envelope(resolution, "remote-signal.json", envelope)

    assert target == tmp_path / ".aef-deposit" / "remote-signal.json"
    assert json.loads(target.read_text(encoding="utf-8"))["protocol"] == DEPOSIT_PROTOCOL
    assert _engine_snapshot(tmp_path) == before


def test_deposit_filename_must_be_single_segment():
    resolution = resolve_cli_workspace(".")
    with pytest.raises(InvalidDepositFilenameError):
        write_deposit_envelope(resolution, "../escape.json", deposit_envelope())
