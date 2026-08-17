from copy import deepcopy
from aef.operations import init_project, migrate_project, upgrade_project


def empty():
    return {"files": {"notes.txt": "project-owned"}}


def _set(project, path, value):
    project["files"][path] = value
    return project


def test_upgrade_preflights_full_path_before_first_mutation():
    _, p, _ = init_project(empty(), instance_id="x")
    migrations = [
        {"id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: _set(x,".agent/a","A")},
        # Missing 1.1.0 -> 1.2.0
    ]
    before = deepcopy(p)
    status, out, meta = upgrade_project(p, target_version="1.2.0", migrations=migrations)
    assert status == "BLOCKED"
    assert meta["reason"] == "migration_path_missing"
    assert meta["applied"] == []
    assert out == before
    assert ".agent/a" not in out["files"]


def test_migration_failure_returns_last_safe_state():
    _, p, _ = init_project(empty(), instance_id="x")
    def explode(x):
        x["files"][".agent/should-not-commit"] = "partial"
        raise RuntimeError("boom")
    migrations = [
        {"id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": explode},
    ]
    before = deepcopy(p)
    status, out, meta = upgrade_project(p, target_version="1.1.0", migrations=migrations)
    assert status == "FAILED"
    assert meta["migration_id"] == "100-110"
    assert meta["error_type"] == "RuntimeError"
    assert out == before
    assert ".agent/should-not-commit" not in out["files"]


def test_missing_ledger_can_be_repaired_from_postcondition_without_replaying_transform():
    _, p, _ = init_project(empty(), instance_id="x")
    # Simulate crash after effects persisted but before ledger commit.
    p["files"][".agent/new-format"] = {"enabled": True}
    calls = {"n": 0}
    def transform(x):
        calls["n"] += 1
        x["files"][".agent/new-format"] = {"enabled": True}
        return x
    post = lambda x: x["files"].get(".agent/new-format") == {"enabled": True}
    status, repaired = migrate_project(p, migration_id="recover-001", transform=transform,
                                       from_version="1.0.0", to_version="1.1.0", postcondition=post)
    assert status == "CHANGE"
    assert calls["n"] == 0
    entry = repaired["files"][".agent/state/migrations.json"]["applied"][-1]
    assert entry["id"] == "recover-001"
    assert entry["recovered_from_postcondition"] is True
    status, twice = migrate_project(repaired, migration_id="recover-001", transform=transform,
                                    from_version="1.0.0", to_version="1.1.0", postcondition=post)
    assert status == "NO_CHANGE"
    assert twice == repaired
    assert calls["n"] == 0


def test_project_owned_content_survives_multiple_framework_versions():
    _, p, _ = init_project(empty(), instance_id="x")
    p["files"]["business/workflow.md"] = "local workflow v1"
    migrations = [
        {"id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: _set(x,".agent/schema/a",1)},
        {"id":"110-120", "from_version":"1.1.0", "to_version":"1.2.0", "transform": lambda x: _set(x,".agent/schema/b",2)},
        {"id":"120-130", "from_version":"1.2.0", "to_version":"1.3.0", "transform": lambda x: _set(x,".agent/schema/c",3)},
    ]
    status, v13, meta = upgrade_project(p, target_version="1.3.0", migrations=migrations)
    assert status == "CHANGE"
    assert meta["applied"] == ["100-110", "110-120", "120-130"]
    assert v13["files"]["business/workflow.md"] == "local workflow v1"
    assert v13["files"]["notes.txt"] == "project-owned"
    status, replay, meta = upgrade_project(v13, target_version="1.3.0", migrations=migrations)
    assert status == "NO_CHANGE"
    assert replay == v13


def test_upgrade_detects_migration_cycle_before_mutation():
    _, p, _ = init_project(empty(), instance_id="x")
    migrations = [
        {"id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: x},
        {"id":"110-100", "from_version":"1.1.0", "to_version":"1.0.0", "transform": lambda x: x},
    ]
    before = deepcopy(p)
    status, out, meta = upgrade_project(p, target_version="1.2.0", migrations=migrations)
    assert status == "BLOCKED"
    assert meta["reason"] == "non_forward_migration"
    assert out == before


def test_duplicate_migration_ids_block_before_mutation():
    _, p, _ = init_project(empty(), instance_id="x")
    before = deepcopy(p)
    migrations = [
        {"id":"dup", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: x},
        {"id":"dup", "from_version":"1.1.0", "to_version":"1.2.0", "transform": lambda x: x},
    ]
    status, out, meta = upgrade_project(p, target_version="1.2.0", migrations=migrations)
    assert status == "BLOCKED"
    assert meta["reason"] == "duplicate_migration_id"
    assert out == before


def test_ambiguous_path_blocks_before_mutation():
    _, p, _ = init_project(empty(), instance_id="x")
    before = deepcopy(p)
    migrations = [
        {"id":"a", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: x},
        {"id":"b", "from_version":"1.0.0", "to_version":"1.0.5", "transform": lambda x: x},
    ]
    status, out, meta = upgrade_project(p, target_version="1.1.0", migrations=migrations)
    assert status == "BLOCKED"
    assert meta["reason"] == "ambiguous_migration_path"
    assert out == before


def test_non_forward_migration_is_rejected():
    _, p, _ = init_project(empty(), instance_id="x")
    migrations = [
        {"id":"bad", "from_version":"1.0.0", "to_version":"1.0.0", "transform": lambda x: x},
    ]
    status, _, meta = upgrade_project(p, target_version="1.1.0", migrations=migrations)
    assert status == "BLOCKED"
    assert meta["reason"] == "non_forward_migration"


def test_postcondition_failure_does_not_commit_migration():
    _, p, _ = init_project(empty(), instance_id="x")
    before = deepcopy(p)
    migrations = [{
        "id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0",
        "transform": lambda x: _set(x,".agent/new-format", {"enabled": False}),
        "postcondition": lambda x: x["files"].get(".agent/new-format") == {"enabled": True},
    }]
    status, out, meta = upgrade_project(p, target_version="1.1.0", migrations=migrations)
    assert status == "FAILED"
    assert meta["reason"] == "migration_postcondition_failed"
    assert out == before


def test_successful_postcondition_is_verified_and_replay_safe():
    _, p, _ = init_project(empty(), instance_id="x")
    migrations = [{
        "id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0",
        "transform": lambda x: _set(x,".agent/new-format", {"enabled": True}),
        "postcondition": lambda x: x["files"].get(".agent/new-format") == {"enabled": True},
    }]
    status, out, _ = upgrade_project(p, target_version="1.1.0", migrations=migrations)
    assert status == "CHANGE"
    status, replay, _ = upgrade_project(out, target_version="1.1.0", migrations=migrations)
    assert status == "NO_CHANGE"
    assert replay == out


def test_atomic_release_discards_schema_progress_when_later_migration_fails():
    from aef.release import apply_framework_release
    _, p, _ = init_project(empty(), instance_id="x")
    before = deepcopy(p)
    migrations = [
        {"id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: _set(x,".agent/a","A")},
        {"id":"110-120", "from_version":"1.1.0", "to_version":"1.2.0", "transform": lambda x: (_ for _ in ()).throw(RuntimeError("crash"))},
    ]
    status, out, meta = apply_framework_release(p, target_version="1.2.0", migrations=migrations)
    assert status == "FAILED"
    assert meta["phase"] == "schema"
    assert out == before
    assert ".agent/a" not in out["files"]
