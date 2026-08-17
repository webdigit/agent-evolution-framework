from copy import deepcopy
from aef.operations import init_project, migrate_project, upgrade_project, audit_project


def empty(): return {"files": {"notes.txt": "keep me"}}


def test_init_creates_seed_and_preserves_unknown_content():
    status, p, meta = init_project(empty(), instance_id="x")
    assert status == "CHANGE"
    assert p["files"]["notes.txt"] == "keep me"
    assert p["files"][".agent/manifest.json"]["instance_id"] == "x"
    assert meta["unresolved_decisions"] == []


def test_init_twice_is_no_change():
    _, p, _ = init_project(empty(), instance_id="x")
    status, p2, _ = init_project(p, instance_id="x")
    assert status == "NO_CHANGE"
    assert p2 == p


def test_init_blocks_on_unresolved_durable_question_without_partial_write():
    source = empty()
    status, p, meta = init_project(source, instance_id="x", required_decisions=["decision.role.primary.v1"])
    assert status == "BLOCKED"
    assert p == source
    assert meta["unresolved_decisions"] == ["decision.role.primary.v1"]
    assert ".agent/manifest.json" not in p["files"]


def test_init_persists_answer_and_does_not_ask_again():
    status, p, _ = init_project(
        empty(), instance_id="x", required_decisions=["decision.role.primary.v1"],
        answers={"decision.role.primary.v1": "support"}
    )
    assert status == "CHANGE"
    status, p2, meta = init_project(p, instance_id="x", required_decisions=["decision.role.primary.v1"])
    assert status == "NO_CHANGE"
    assert meta["unresolved_decisions"] == []


def test_migrate_is_replay_safe_and_preserves_unknown_content():
    p = empty()
    def transform(x):
        x["files"][".agent/knowledge/imported.txt"] = "legacy"
        return x
    status, once = migrate_project(p, migration_id="legacy-001", transform=transform)
    assert status == "CHANGE"
    assert once["files"]["notes.txt"] == "keep me"
    status, twice = migrate_project(once, migration_id="legacy-001", transform=transform)
    assert status == "NO_CHANGE"
    assert twice == once


def test_upgrade_applies_ordered_path_and_twice_is_no_change():
    _, p, _ = init_project(empty(), instance_id="x")
    migrations = [
        {"id":"schema-100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: _set(x,".agent/new-a","A")},
        {"id":"schema-110-120", "from_version":"1.1.0", "to_version":"1.2.0", "transform": lambda x: _set(x,".agent/new-b","B")},
    ]
    status, upgraded, meta = upgrade_project(p, target_version="1.2.0", migrations=migrations)
    assert status == "CHANGE"
    assert meta["applied"] == ["schema-100-110","schema-110-120"]
    assert upgraded["files"][".agent/manifest.json"]["schema_version"] == "1.2.0"
    status, twice, meta = upgrade_project(upgraded, target_version="1.2.0", migrations=migrations)
    assert status == "NO_CHANGE"
    assert twice == upgraded
    assert meta["applied"] == []


def _set(project, path, value):
    project["files"][path] = value
    return project


def test_upgrade_refuses_downgrade():
    _, p, _ = init_project(empty(), instance_id="x")
    p["files"][".agent/manifest.json"]["schema_version"] = "1.2.0"
    status, out, meta = upgrade_project(p, target_version="1.0.0", migrations=[])
    assert status == "BLOCKED"
    assert out == p
    assert meta["reason"] == "implicit_downgrade_forbidden"


def test_upgrade_blocks_on_missing_path_without_inventing_migration():
    _, p, _ = init_project(empty(), instance_id="x")
    status, out, meta = upgrade_project(p, target_version="1.2.0", migrations=[])
    assert status == "BLOCKED"
    assert meta["reason"] == "migration_path_missing"
    assert out["files"][".agent/manifest.json"]["schema_version"] == "1.0.0"


def test_audit_is_read_only_and_repeatable():
    _, p, _ = init_project(empty(), instance_id="x")
    before = deepcopy(p)
    a = audit_project(p)
    b = audit_project(p)
    assert a == b
    assert p == before
    assert a == {
        "status": "FAIL",
        "schema_version": "1.0.0",
        "findings": [
            {"id": "missing-knowledge-state", "severity": "error"},
        ],
    }
