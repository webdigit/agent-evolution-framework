from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aef import cli
from aef.record_document import build_persisted_record, validate_record_submission


SENTINEL = "SENTINEL_AEF_COMPETENCY_DECLARE_ISOLATION"


def write_json(path: Path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def test_declaration_ignores_exterior_memory_and_env(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    exterior = home / ".agent" / "state"
    exterior.mkdir(parents=True)
    (exterior / "competencies.json").write_text(
        json.dumps({"leaked": {"level": "L5", "xp": 99, "trust": 1.0, "title": SENTINEL}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AEF_SENTINEL", SENTINEL)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invoke(
        capsys, "--json", "--workspace", str(workspace), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    )
    recording = write_json(workspace / "recording.json", {
        "protocol": "aef.record.submit/v1",
        "record_id": "session-alpha",
        "recorded_at": "2026-08-20T13:21:00Z",
        "declared_by": {"kind": "human", "identifier": "operator"},
        "payload": {
            "context": "local only",
            "actions": [{"summary": "kept work inside the workspace"}],
            "outcomes": [],
            "incidents": [],
            "evidence": [],
        },
    })
    invoke(
        capsys, "--json", "--workspace", str(workspace),
        "record", "--recording", str(recording),
    )
    persisted = build_persisted_record(
        validate_record_submission(json.loads(recording.read_text(encoding="utf-8")))
    )
    declaration = write_json(workspace / "declaration.json", {
        "protocol": "aef.competency.declare.submit/v1",
        "competency_id": "local-skill",
        "title": "Local skill",
        "scope": "workspace only",
        "limits": "no exterior memory",
        "rationale": "isolation proof",
        "records": [{"record_id": persisted["record_id"], "digest": persisted["digest"]}],
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    })
    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(workspace),
        "competency", "declare", "--declaration", str(declaration),
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err
    competencies = json.loads(
        (workspace / ".agent" / "state" / "competencies.json").read_text(encoding="utf-8")
    )
    assert "leaked" not in competencies
    assert "local-skill" in competencies
    exterior_text = (exterior / "competencies.json").read_text(encoding="utf-8")
    assert "local-skill" not in exterior_text


@pytest.mark.skipif(os.name == "nt" and not hasattr(os, "symlink"), reason="symlink unavailable")
def test_symlink_record_is_blocked(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    invoke(
        capsys, "--json", "--workspace", str(workspace), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    )
    exterior = tmp_path / "outside-record.json"
    exterior.write_text("{}", encoding="utf-8")
    records = workspace / ".agent" / "records"
    records.mkdir(parents=True, exist_ok=True)
    link = records / "session-alpha.json"
    try:
        link.symlink_to(exterior)
    except OSError:
        pytest.skip("symlink creation requires privileges")
    declaration = write_json(workspace / "declaration.json", {
        "protocol": "aef.competency.declare.submit/v1",
        "competency_id": "linked-skill",
        "title": "Linked",
        "scope": "s",
        "limits": "l",
        "rationale": "r",
        "records": [{
            "record_id": "session-alpha",
            "digest": "sha256:" + ("a" * 64),
        }],
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    })
    before = (workspace / ".agent" / "state" / "competencies.json").read_bytes()
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(workspace),
        "competency", "declare", "--declaration", str(declaration),
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert (workspace / ".agent" / "state" / "competencies.json").read_bytes() == before
