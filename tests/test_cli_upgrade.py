import json
from pathlib import Path

import pytest

from aef import cli
from aef.filesystem import load_workspace
from aef.upgrade_compat import UPGRADE_TRANSACTION_PATH, TARGET_WORKSPACE_SCHEMA_VERSION
from aef.upgrade_plan import MigrationSpec
from tests.test_upgrade_plan import synthetic_registry


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    payload = captured.out.strip()
    envelope = json.loads(payload) if payload.startswith("{") else None
    return code, envelope, captured


def init_workspace(tmp_path, capsys):
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "init", "--instance-id", "agent-1", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    return tmp_path


def snapshot(root: Path):
    files = {}
    agent = root / ".agent"
    if not agent.exists():
        return files
    for path in sorted(p for p in agent.rglob("*") if p.is_file()):
        files[path.relative_to(root).as_posix()] = (
            path.stat().st_mtime_ns, path.read_bytes()
        )
    return files


def test_check_and_dry_run_no_change_on_valid_v1(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    before = snapshot(tmp_path)
    for extra in (("--check",), ("--dry-run",)):
        code, envelope, _ = invoke(
            capsys, "--json", "--workspace", str(tmp_path), "upgrade", *extra,
        )
        assert code == 0
        assert envelope["command"] == "UPGRADE"
        assert envelope["status"] == "NO_CHANGE"
        assert envelope["result"]["target_schema_version"] == TARGET_WORKSPACE_SCHEMA_VERSION
        assert envelope["result"]["current_schema_version"] == "1.0.0"
        assert envelope["result"]["installed_package_version"]
        assert envelope["result"]["workspace_framework_version"]
        assert "target-schema" not in envelope["result"]
    assert snapshot(tmp_path) == before
    assert not (tmp_path / ".agent" / "state" / "upgrade-transaction.json").exists()


def test_uninitialized_is_blocked_without_writes(tmp_path, capsys):
    before = list(tmp_path.iterdir())
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--check",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert list(tmp_path.iterdir()) == before


def test_synthetic_check_change_writes_nothing(tmp_path, capsys, monkeypatch):
    init_workspace(tmp_path, capsys)
    monkeypatch.setattr("aef.upgrade_ops.ordered_migrations", synthetic_registry)
    monkeypatch.setattr(
        "aef.upgrade_plan.TARGET_WORKSPACE_SCHEMA_VERSION", "1.2.0",
    )
    monkeypatch.setattr(
        "aef.upgrade_compat.TARGET_WORKSPACE_SCHEMA_VERSION", "1.2.0",
    )
    monkeypatch.setattr(
        "aef.upgrade_ops.TARGET_WORKSPACE_SCHEMA_VERSION", "1.2.0",
    )
    before = snapshot(tmp_path)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--check",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert envelope["result"]["migration_ids"] == [
        "test.1.0.0-1.1.0", "test.1.1.0-1.2.0",
    ]
    assert snapshot(tmp_path) == before


def test_apply_replay_and_ledger(tmp_path, capsys, monkeypatch):
    init_workspace(tmp_path, capsys)
    monkeypatch.setattr("aef.upgrade_ops.ordered_migrations", synthetic_registry)
    monkeypatch.setattr("aef.upgrade_ops.TARGET_WORKSPACE_SCHEMA_VERSION", "1.2.0")
    monkeypatch.setattr("aef.upgrade_compat.TARGET_WORKSPACE_SCHEMA_VERSION", "1.2.0")
    monkeypatch.setattr("aef.upgrade_plan.TARGET_WORKSPACE_SCHEMA_VERSION", "1.2.0")

    first, first_env, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "upgrade")
    second, second_env, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "upgrade")
    assert first == 0 and second == 0
    assert first_env["status"] == "CHANGE"
    assert second_env["status"] == "NO_CHANGE"
    stored = load_workspace(tmp_path)
    assert stored["files"][".agent/manifest.json"]["schema_version"] == "1.2.0"
    ledger = stored["files"][".agent/state/migrations.json"]
    assert {entry["id"] for entry in ledger["applied"]} >= {
        "test.1.0.0-1.1.0", "test.1.1.0-1.2.0",
    }
    assert UPGRADE_TRANSACTION_PATH not in stored["files"]
    instance = stored["files"][".agent/manifest.json"]["instance_id"]
    created = stored["files"][".agent/manifest.json"]["created_at"]
    assert instance == "agent-1"
    assert created == "2026-08-14T10:00:00Z"


def test_upgrade_journal_blocks_check_until_recover(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    journal = tmp_path / ".agent" / "state" / "upgrade-transaction.json"
    journal.write_text("{", encoding="utf-8")
    before = snapshot(tmp_path)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--check",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "upgrade_recovery_required"
    assert snapshot(tmp_path) == before

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "upgrade", "--recover", "--dry-run",
    )
    assert code == 4
    assert envelope["result"]["recovery_action"] == "inspect"


def test_record_blocked_when_upgrade_journal_present(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    (tmp_path / ".agent" / "state" / "upgrade-transaction.json").write_text("{", encoding="utf-8")
    recording = tmp_path / "recording.json"
    recording.write_text(json.dumps({
        "protocol": "aef.record.submit/v1",
        "record_id": "session-alpha",
        "recorded_at": "2026-08-20T13:21:00Z",
        "declared_by": {"kind": "human", "identifier": "operator"},
        "payload": {
            "context": "x",
            "actions": [{"summary": "y"}],
            "outcomes": [],
            "incidents": [],
            "evidence": [],
        },
    }), encoding="utf-8")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(recording),
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "upgrade_recovery_required"


def test_audit_reports_upgrade_recovery(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    (tmp_path / ".agent" / "state" / "upgrade-transaction.json").write_text("{", encoding="utf-8")
    code, envelope, _ = invoke(capsys, "--json", "--workspace", str(tmp_path), "audit")
    assert envelope["command"] == "AUDIT"
    ids = {item["id"] for item in envelope["result"]["findings"]}
    assert "upgrade-recovery-required" in ids
    assert "evaluation-recovery-required" not in ids


def test_init_record_evaluate_audit_do_not_upgrade(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(
        "aef.cli.run_upgrade",
        lambda *args, **kwargs: called.append(True) or ("NO_CHANGE", {}, {}),
    )
    init_workspace(tmp_path, capsys)
    invoke(capsys, "--json", "--workspace", str(tmp_path), "audit")
    assert called == []


def test_no_target_schema_flag(tmp_path, capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main([
            "--json", "--workspace", str(tmp_path),
            "upgrade", "--target-schema", "1.2.0",
        ])
    assert exited.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_recover_prepared_then_replay_is_safe(tmp_path, capsys):
    from aef.upgrade_transaction import (
        build_upgrade_transaction,
        filesystem_json,
        sha256_text,
    )

    init_workspace(tmp_path, capsys)
    stored = load_workspace(tmp_path)
    manifest = stored["files"][".agent/manifest.json"]
    disk = (tmp_path / ".agent" / "manifest.json").read_text(encoding="utf-8")
    entry = {
        "path": ".agent/manifest.json",
        "before_content": disk,
        "after_content": disk,
        "before_hash": sha256_text(disk),
        "after_hash": sha256_text(disk),
    }
    journal = build_upgrade_transaction(
        workspace_instance_id=manifest["instance_id"],
        from_schema_version="1.0.0",
        to_schema_version="1.0.0",
        migration_ids=[],
        files=[entry],
        created_paths=[],
        phase="prepared",
    )
    journal_path = tmp_path / ".agent" / "state" / "upgrade-transaction.json"
    journal_path.write_text(filesystem_json(journal), encoding="utf-8")

    dry_code, dry, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "upgrade", "--recover", "--dry-run",
    )
    assert dry_code == 0
    assert dry["status"] == "CHANGE"
    assert dry["result"]["recovery_action"] == "rollback"
    assert journal_path.is_file()

    apply_code, applied, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--recover",
    )
    assert apply_code == 0
    assert applied["status"] == "CHANGE"
    assert not journal_path.exists()

    replay_code, replay, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--recover",
    )
    assert replay_code == 0
    assert replay["status"] == "NO_CHANGE"
