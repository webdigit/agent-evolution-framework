import json
from pathlib import Path

from aef import cli
from aef.record_document import build_persisted_record
from aef.record_store import persist_record


# Hors V1: UI, chunks, native cost measurement, dashboard, journal consumption.


FORBIDDEN_NAMES = (
    "ingest_events",
    "record_outcome",
    "handle_task",
    "career_cycle_step",
)
RECORD_MODULES = (
    "src/aef/record_document.py",
    "src/aef/record_store.py",
    "src/aef/record_audit.py",
)
STATE_FILES = (
    ".agent/state/career.json",
    ".agent/state/competencies.json",
    ".agent/knowledge/knowledge.json",
    ".agent/state/evaluations.json",
    ".agent/state/decisions.json",
)


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


def init_workspace(tmp_path: Path, capsys):
    code = cli.main([
        "--json", "--workspace", str(tmp_path), "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    ])
    capsys.readouterr()
    assert code == 0
    return tmp_path


def snapshot_agent(root: Path) -> dict[str, bytes]:
    agent = root / ".agent"
    if not agent.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in agent.rglob("*")
        if path.is_file()
    }


def test_record_modules_do_not_name_forbidden_hooks():
    root = Path(__file__).resolve().parents[1]
    for relative in RECORD_MODULES:
        text = (root / relative).read_text(encoding="utf-8")
        for name in FORBIDDEN_NAMES:
            assert name not in text


def test_init_has_no_records_then_first_apply_creates_them(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    assert not (tmp_path / ".agent" / "records").exists()
    code = cli.main(["--json", "--workspace", str(tmp_path), "audit"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert envelope["status"] == "PASS"

    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps(submission()), encoding="utf-8")
    code = cli.main([
        "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    ])
    capsys.readouterr()
    assert code == 0
    assert (tmp_path / ".agent" / "records" / "session-alpha.json").is_file()


def test_record_does_not_mutate_career_knowledge_or_competencies(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    before = snapshot_agent(tmp_path)
    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps(submission()), encoding="utf-8")

    cli.main(["--json", "--workspace", str(tmp_path), "record", "--recording", str(recording)])
    capsys.readouterr()
    cli.main(["--json", "--workspace", str(tmp_path), "record", "--recording", str(recording)])
    capsys.readouterr()

    after = snapshot_agent(tmp_path)
    assert set(after) - set(before) == {".agent/records/session-alpha.json"}
    for relative in STATE_FILES:
        assert after[relative] == before[relative]
    assert not (tmp_path / ".agent" / "state" / "evaluation-transaction.json").exists()


def test_dry_run_does_not_create_records_on_initialized_workspace(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps(submission()), encoding="utf-8")
    cli.main([
        "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording), "--dry-run",
    ])
    capsys.readouterr()
    assert not (tmp_path / ".agent" / "records").exists()


def test_agent_and_human_share_the_same_persist_path(tmp_path):
    human = build_persisted_record(submission())
    agent_doc = submission()
    agent_doc["declared_by"] = {"kind": "agent", "identifier": "operator-agent"}
    agent_doc["record_id"] = "session-beta"
    agent = build_persisted_record(agent_doc)

    persist_record(tmp_path, human)
    persist_record(tmp_path, agent)
    assert (tmp_path / ".agent" / "records" / "session-alpha.json").is_file()
    assert (tmp_path / ".agent" / "records" / "session-beta.json").is_file()
    stored = json.loads((tmp_path / ".agent" / "records" / "session-beta.json").read_text(encoding="utf-8"))
    assert stored["declared_by"]["kind"] == "agent"
    assert "authority" not in stored["declared_by"]


def test_omitted_metrics_stay_omitted(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps(submission()), encoding="utf-8")
    cli.main(["--json", "--workspace", str(tmp_path), "record", "--recording", str(recording)])
    capsys.readouterr()
    stored = json.loads((tmp_path / ".agent" / "records" / "session-alpha.json").read_text(encoding="utf-8"))
    assert "external_metrics" not in stored
    assert "duration" not in stored
    assert stored.get("payload", {}).get("cost") is None


def test_forbidden_engines_are_not_invoked(tmp_path, monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise AssertionError("RECORD must not call learning or progression engines")

    monkeypatch.setattr("aef.learning_engine.ingest_events", boom)
    monkeypatch.setattr("aef.progression.record_outcome", boom)
    init_workspace(tmp_path, capsys)
    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps(submission()), encoding="utf-8")
    code = cli.main([
        "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    ])
    capsys.readouterr()
    assert code == 0


def test_audit_after_record_has_no_score_findings(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps(submission()), encoding="utf-8")
    cli.main(["--json", "--workspace", str(tmp_path), "record", "--recording", str(recording)])
    capsys.readouterr()
    code = cli.main(["--json", "--workspace", str(tmp_path), "audit"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert envelope["status"] == "PASS"
    ids = [item.get("id") for item in envelope["result"]["findings"]]
    assert not any("score" in (item or "") or "promotion" in (item or "") for item in ids)
