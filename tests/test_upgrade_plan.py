from copy import deepcopy

import pytest

from aef.upgrade_compat import TARGET_WORKSPACE_SCHEMA_VERSION
from aef.upgrade_plan import (
    MigrationFailure,
    MigrationSpec,
    apply_plan_in_memory,
    enforce_content_bounds,
    json_depth,
    plan_upgrade,
    validate_ledger,
)


def _identity(project):
    return deepcopy(project)


def _mark(version):
    def transform(project):
        out = deepcopy(project)
        out.setdefault("files", {}).setdefault(".agent/manifest.json", {})
        out["files"][".agent/manifest.json"]["schema_version"] = version
        out["files"][".agent/manifest.json"]["test_marker"] = version
        return out
    return transform


def synthetic_registry():
    return (
        MigrationSpec(
            "test.1.0.0-1.1.0", "1.0.0", "1.1.0",
            (".agent/manifest.json",), _mark("1.1.0"),
        ),
        MigrationSpec(
            "test.1.1.0-1.2.0", "1.1.0", "1.2.0",
            (".agent/manifest.json",), _mark("1.2.0"),
        ),
    )


def test_target_is_one_zero_zero():
    assert TARGET_WORKSPACE_SCHEMA_VERSION == "1.0.0"


def test_plan_no_change_at_target():
    plan = plan_upgrade("1.0.0", migrations=synthetic_registry())
    assert plan.status == "NO_CHANGE"
    assert plan.migrations == ()


def test_synthetic_registry_is_not_public_and_plans_unique_path():
    from aef.upgrade_compat import production_migrations
    assert production_migrations() == ()
    plan = plan_upgrade("1.0.0", target_schema_version="1.2.0", migrations=synthetic_registry())
    assert plan.status == "CHANGE"
    assert plan.migration_ids == ["test.1.0.0-1.1.0", "test.1.1.0-1.2.0"]


@pytest.mark.parametrize("current,target,reason", [
    ("1.9.0", "1.0.0", "future_schema"),
    ("1.2.0", "1.0.0", "future_schema"),
    ("0.9.0", "1.0.0", "migration_path_missing"),
])
def test_blocked_version_classes(current, target, reason):
    plan = plan_upgrade(current, target_schema_version=target, migrations=synthetic_registry())
    assert plan.status == "BLOCKED"
    assert plan.reason == reason


def test_ambiguous_and_cycle_and_ledger_conflict():
    ambiguous = (
        MigrationSpec("a", "1.0.0", "1.1.0", (".agent/manifest.json",), _identity),
        MigrationSpec("b", "1.0.0", "1.1.0", (".agent/manifest.json",), _identity),
    )
    plan = plan_upgrade("1.0.0", target_schema_version="1.1.0", migrations=ambiguous)
    assert plan.status == "BLOCKED"
    assert plan.reason == "ambiguous_migration_path"

    cycle = (
        MigrationSpec("c1", "1.0.0", "1.0.1", (".agent/manifest.json",), _identity),
        MigrationSpec("c2", "1.0.1", "1.0.0", (".agent/manifest.json",), _identity),
    )
    # non_forward is detected before cycle on the second hop
    plan = plan_upgrade("1.0.0", target_schema_version="1.2.0", migrations=cycle)
    assert plan.status == "BLOCKED"

    spec = synthetic_registry()[0]
    ledger = {
        "applied": [{
            "id": spec.id,
            "from_version": "9.9.9",
            "to_version": "9.9.10",
            "status": "applied",
            "migration_fingerprint": "sha256:" + ("ab" * 32),
        }]
    }
    plan = plan_upgrade(
        "1.0.0", target_schema_version="1.1.0",
        migrations=(spec,), ledger=ledger,
    )
    assert plan.status == "BLOCKED"
    assert plan.reason == "ledger_conflict"


def test_old_ledger_remains_valid():
    assert validate_ledger({"applied": []}) == {"applied": []}
    assert validate_ledger({
        "applied": [{
            "id": "legacy",
            "from_version": "0.1.0",
            "to_version": "1.0.0",
            "status": "applied",
        }]
    })["applied"][0]["id"] == "legacy"


def test_bounds_min_and_max():
    assert json_depth({"a": {"b": 1}}) == 2
    assert enforce_content_bounds({".agent/manifest.json": {"ok": True}}, [".agent/manifest.json"]) is None
    huge = "x" * (10 * 1024 * 1024 + 1)
    assert enforce_content_bounds({".agent/manifest.json": huge}, [".agent/manifest.json"]) == "file_size_exceeded"


def test_apply_in_memory_and_migration_failure():
    project = {"files": {".agent/manifest.json": {"schema_version": "1.0.0", "framework": "aef"}}}
    plan = plan_upgrade("1.0.0", target_schema_version="1.2.0", migrations=synthetic_registry())
    out = apply_plan_in_memory(project, plan)
    assert out["files"][".agent/manifest.json"]["schema_version"] == "1.2.0"

    def boom(project):
        raise MigrationFailure("test.1.0.0-1.1.0", "declared")

    failing = (MigrationSpec(
        "test.1.0.0-1.1.0", "1.0.0", "1.1.0", (".agent/manifest.json",), boom,
    ),)
    plan = plan_upgrade("1.0.0", target_schema_version="1.1.0", migrations=failing)
    with pytest.raises(MigrationFailure):
        apply_plan_in_memory(project, plan)


def test_missing_workspace_is_blocked():
    plan = plan_upgrade(None)
    assert plan.status == "BLOCKED"
    assert plan.reason == "workspace_not_initialized"
