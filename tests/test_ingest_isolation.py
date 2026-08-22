from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aef import cli
from tests.test_cli_ingest import (
    intake_for,
    init_workspace,
    invoke,
    persist_sample_record,
    write_json,
)


SENTINEL = "AEF_INGEST_SENTINEL_DO_NOT_LEAK_9f2a"
SENTINEL_PATH = "C:/sentinel-outside/memory.json"


def test_ingest_and_audit_never_leak_exterior_sentinels(tmp_path, capsys, monkeypatch):
    exterior = tmp_path / "outside"
    exterior.mkdir()
    (exterior / "memory.json").write_text(SENTINEL, encoding="utf-8")
    (exterior / "records.json").write_text(
        json.dumps({"record_id": "foreign", "secret": SENTINEL}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEF_FAKE_CONNECTOR_TOKEN", SENTINEL)
    monkeypatch.setenv("NOTION_TOKEN", SENTINEL)
    workspace = tmp_path / "project with spaces"
    workspace.mkdir()
    init_workspace(workspace, capsys)
    persisted = persist_sample_record(workspace, capsys)
    intake = write_json(workspace / "intake 日本.json", intake_for(persisted))

    for args in (
        ("ingest", "--intake", str(intake), "--dry-run"),
        ("ingest", "--intake", str(intake)),
        ("audit",),
    ):
        code, envelope, captured = invoke(
            capsys, "--json", "--workspace", str(workspace), *args,
        )
        dumped = json.dumps(envelope) + captured.out + captured.err
        assert SENTINEL not in dumped
        assert SENTINEL_PATH not in dumped
        assert str(exterior) not in dumped
        assert str(Path.home()) not in dumped
        assert code in {0, 4}
    assert list(exterior.iterdir())


def test_symlink_outside_workspace_blocks_without_knowledge_write(tmp_path, capsys):
    exterior = tmp_path / "outside-record.json"
    exterior.write_text("{}", encoding="utf-8")
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    target = tmp_path / ".agent" / "records" / "session-alpha.json"
    knowledge = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    before = knowledge.read_bytes()
    target.unlink()
    try:
        target.symlink_to(exterior)
    except (OSError, NotImplementedError):
        if os.name == "nt":
            pytest.skip("symlink creation requires privilege on this host")
        raise
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert knowledge.read_bytes() == before
