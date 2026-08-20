import json
from pathlib import Path

import pytest

from aef.filesystem import load_workspace
from aef.upgrade_compat import MANIFEST_PATH, UPGRADE_TRANSACTION_PATH
from aef.upgrade_ops import audit_upgrade_findings, recover_upgrade
from aef.upgrade_transaction import (
    InvalidUpgradeTransactionError,
    TRANSACTION_PROTOCOL,
    build_upgrade_transaction,
    compute_transaction_id,
    filesystem_json,
    sha256_text,
    validate_upgrade_transaction,
)
from tests.test_cli_upgrade import init_workspace, invoke, snapshot


HOSTILE_PATHS = (
    "../../outside/secret.json",
    "../outside/secret.json",
    ".agent/../external.json",
    ".agent/state/../../outside.json",
    "C:/outside/secret.json",
    r"C:\outside\secret.json",
    "//server/share/secret.json",
    r".agent\state\x.json",
    ".agent//state/x.json",
    ".agent/state/value.txt:stream",
    ".agent/CON",
    UPGRADE_TRANSACTION_PATH,
    ".agent/state/Upgrade-Transaction.json",
    "/tmp/aef-outside.txt",
    ".agent/state/COM1.log",
    ".agent/state/trailing.",
)


def _unsigned_journal(path: str, *, instance_id="agent-1") -> dict:
    content = "hostile-payload\n"
    entry = {
        "path": path,
        "before_content": content,
        "after_content": content,
        "before_hash": sha256_text(content),
        "after_hash": sha256_text(content),
    }
    body = {
        "protocol": TRANSACTION_PROTOCOL,
        "workspace_instance_id": instance_id,
        "from_schema_version": "1.0.0",
        "to_schema_version": "1.0.0",
        "phase": "prepared",
        "migration_ids": [],
        "paths": [path],
        "created_paths": [],
        "files": [entry],
    }
    body["transaction_id"] = compute_transaction_id(body)
    return body


def _file_entry(path: str, before: str, after: str, *, created=False) -> dict[str, str]:
    return {
        "path": path,
        "before_content": "" if created else before,
        "after_content": after,
        "before_hash": sha256_text("" if created else before),
        "after_hash": sha256_text(after),
    }


def _write_journal(root: Path, journal: dict) -> Path:
    path = root / ".agent" / "state" / "upgrade-transaction.json"
    path.write_text(filesystem_json(journal), encoding="utf-8")
    return path


def _manifest_texts(root: Path) -> tuple[str, str, dict]:
    stored = load_workspace(root)
    manifest = stored["files"][MANIFEST_PATH]
    before = (root / ".agent" / "manifest.json").read_text(encoding="utf-8")
    after_obj = json.loads(before)
    after_obj["schema_version"] = "1.1.0"
    after_obj["test_marker"] = "after"
    after = filesystem_json(after_obj)
    return before, after, manifest


@pytest.mark.parametrize("path", HOSTILE_PATHS)
def test_validate_rejects_hostile_journal_paths(path):
    with pytest.raises(InvalidUpgradeTransactionError):
        validate_upgrade_transaction(_unsigned_journal(path))


def test_validate_rejects_case_collisions():
    content = "x\n"
    files = [
        _file_entry(".agent/state/Foo.json", content, content),
        _file_entry(".agent/state/foo.json", content, "y\n"),
    ]
    body = {
        "protocol": TRANSACTION_PROTOCOL,
        "workspace_instance_id": "agent-1",
        "from_schema_version": "1.0.0",
        "to_schema_version": "1.0.0",
        "phase": "prepared",
        "migration_ids": [],
        "paths": [entry["path"] for entry in files],
        "created_paths": [],
        "files": files,
    }
    body["transaction_id"] = compute_transaction_id(body)
    with pytest.raises(InvalidUpgradeTransactionError):
        validate_upgrade_transaction(body)


@pytest.mark.parametrize("path", HOSTILE_PATHS)
def test_recover_and_audit_ignore_hostile_paths(tmp_path, capsys, path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    secret = outside / "secret.json"
    secret.write_text("OUTSIDE_SECRET_DO_NOT_READ\n", encoding="utf-8")
    init_workspace(project, capsys)
    _write_journal(project, _unsigned_journal(path))
    planted = snapshot(project)

    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(project), "upgrade", "--recover",
    )
    dumped = json.dumps(envelope) + captured.out + captured.err
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "invalid_upgrade_transaction"
    assert "OUTSIDE_SECRET_DO_NOT_READ" not in dumped
    assert snapshot(project) == planted
    assert (project / ".agent" / "state" / "upgrade-transaction.json").is_file()
    assert secret.read_text(encoding="utf-8") == "OUTSIDE_SECRET_DO_NOT_READ\n"

    findings = {
        item["id"]
        for item in audit_upgrade_findings(load_workspace(project), root=project)
    }
    assert "upgrade-recovery-required" in findings
    assert "upgrade-journal-malformed" in findings


def test_copied_journal_from_other_workspace_is_blocked(tmp_path, capsys):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    init_workspace(first, capsys)
    invoke(
        capsys, "--json", "--workspace", str(second),
        "init", "--instance-id", "agent-2", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    before, after, manifest = _manifest_texts(first)
    journal = build_upgrade_transaction(
        workspace_instance_id=manifest["instance_id"],
        from_schema_version="1.0.0",
        to_schema_version="1.1.0",
        migration_ids=["test.1.0.0-1.1.0"],
        files=[_file_entry(MANIFEST_PATH, before, after)],
        created_paths=[],
        phase="prepared",
    )
    _write_journal(second, journal)
    before_snap = snapshot(second)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(second), "upgrade", "--recover",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "upgrade_workspace_mismatch"
    assert snapshot(second) == before_snap
    ids = {
        item["id"]
        for item in audit_upgrade_findings(load_workspace(second), root=second)
    }
    assert "upgrade-workspace-mismatch" in ids
    assert "upgrade-recovery-required" in ids


def test_journal_version_mismatch_is_blocked(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    knowledge = (tmp_path / ".agent" / "knowledge" / "knowledge.json").read_text(
        encoding="utf-8",
    )
    journal = build_upgrade_transaction(
        workspace_instance_id="agent-1",
        from_schema_version="1.0.0",
        to_schema_version="1.1.0",
        migration_ids=["test.1.0.0-1.1.0"],
        files=[_file_entry(".agent/knowledge/knowledge.json", knowledge, knowledge + " ")],
        created_paths=[],
        phase="prepared",
    )
    manifest_path = tmp_path / ".agent" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "2.0.0"
    manifest_path.write_text(filesystem_json(manifest), encoding="utf-8")
    _write_journal(tmp_path, journal)
    before_snap = snapshot(tmp_path)
    status, result, extra = recover_upgrade(tmp_path)
    assert status == "BLOCKED"
    assert extra["reason"] == "upgrade_version_mismatch"
    assert snapshot(tmp_path) == before_snap
    ids = {
        item["id"]
        for item in audit_upgrade_findings(load_workspace(tmp_path), root=tmp_path)
    }
    assert "upgrade-version-mismatch" in ids


def test_incompatible_profile_blocks_plan(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    manifest_path = tmp_path / ".agent" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["framework_version"] = "0.1.0"
    manifest_path.write_text(filesystem_json(manifest), encoding="utf-8")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--check",
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "incompatible_profile"


def test_prepared_after_journal_only_rolls_back(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    before, after, manifest = _manifest_texts(tmp_path)
    journal = build_upgrade_transaction(
        workspace_instance_id=manifest["instance_id"],
        from_schema_version="1.0.0",
        to_schema_version="1.1.0",
        migration_ids=["test.1.0.0-1.1.0"],
        files=[_file_entry(MANIFEST_PATH, before, after)],
        created_paths=[],
        phase="prepared",
    )
    _write_journal(tmp_path, journal)
    invoke(capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--recover")
    assert (tmp_path / ".agent" / "manifest.json").read_text(encoding="utf-8") == before
    assert not (tmp_path / ".agent" / "state" / "upgrade-transaction.json").exists()


def test_prepared_partial_write_is_inconsistent(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    before, after, manifest = _manifest_texts(tmp_path)
    knowledge_path = tmp_path / ".agent" / "knowledge" / "knowledge.json"
    knowledge_before = knowledge_path.read_text(encoding="utf-8")
    knowledge_after = filesystem_json({"migrated": True})
    files = [
        _file_entry(".agent/knowledge/knowledge.json", knowledge_before, knowledge_after),
        _file_entry(MANIFEST_PATH, before, after),
    ]
    journal = build_upgrade_transaction(
        workspace_instance_id=manifest["instance_id"],
        from_schema_version="1.0.0",
        to_schema_version="1.1.0",
        migration_ids=["test.1.0.0-1.1.0"],
        files=sorted(files, key=lambda item: item["path"]),
        created_paths=[],
        phase="prepared",
    )
    _write_journal(tmp_path, journal)
    knowledge_path.write_text(knowledge_after, encoding="utf-8")
    before_snap = snapshot(tmp_path)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--recover",
    )
    assert code == 4
    assert envelope["meta"]["reason"] == "upgrade_transaction_inconsistent"
    assert snapshot(tmp_path) == before_snap


def test_prepared_all_after_rolls_back(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    before, after, manifest = _manifest_texts(tmp_path)
    journal = build_upgrade_transaction(
        workspace_instance_id=manifest["instance_id"],
        from_schema_version="1.0.0",
        to_schema_version="1.1.0",
        migration_ids=["test.1.0.0-1.1.0"],
        files=[_file_entry(MANIFEST_PATH, before, after)],
        created_paths=[],
        phase="prepared",
    )
    _write_journal(tmp_path, journal)
    (tmp_path / ".agent" / "manifest.json").write_text(after, encoding="utf-8")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--recover",
    )
    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert envelope["result"]["recovery_action"] == "rollback"
    assert (tmp_path / ".agent" / "manifest.json").read_text(encoding="utf-8") == before
    assert not (tmp_path / ".agent" / "state" / "upgrade-transaction.json").exists()


def test_committed_all_after_finalizes(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    before, after, manifest = _manifest_texts(tmp_path)
    journal = build_upgrade_transaction(
        workspace_instance_id=manifest["instance_id"],
        from_schema_version="1.0.0",
        to_schema_version="1.1.0",
        migration_ids=["test.1.0.0-1.1.0"],
        files=[_file_entry(MANIFEST_PATH, before, after)],
        created_paths=[],
        phase="committed",
    )
    _write_journal(tmp_path, journal)
    (tmp_path / ".agent" / "manifest.json").write_text(after, encoding="utf-8")
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path), "upgrade", "--recover",
    )
    assert code == 0
    assert envelope["result"]["recovery_action"] == "finalize"
    assert (tmp_path / ".agent" / "manifest.json").read_text(encoding="utf-8") == after
    assert not (tmp_path / ".agent" / "state" / "upgrade-transaction.json").exists()
