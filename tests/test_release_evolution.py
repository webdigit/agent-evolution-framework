from copy import deepcopy
from aef.operations import init_project
from aef.ownership import capture_managed_inventory
from aef.release import apply_framework_release


def _set(project, path, value):
    project["files"][path] = value
    return project


def base_project():
    _, p, _ = init_project({"files": {"project.md": "identity"}}, instance_id="seed-1")
    p["files"][".agent/role/mission.json"] = {"mission": "serve a neutral project"}
    p["files"][".agent/state/competencies.json"] = {"analysis": {"level":"L3","xp":240,"trust":0.93}}
    p["files"][".agent/knowledge/rules.json"] = [{"id":"rule-1","status":"active","text":"verify before acting"}]
    p["files"][".agent/integrations/registry.json"] = {"connectors":[{"id":"arbitrary-x","status":"available","capabilities":[]}]}
    return p


def migrations():
    return [
        {"id":"100-110", "from_version":"1.0.0", "to_version":"1.1.0", "transform": lambda x: _set(x,".agent/schema/a",1)},
        {"id":"110-120", "from_version":"1.1.0", "to_version":"1.2.0", "transform": lambda x: _set(x,".agent/schema/b",2)},
    ]


def install_inventory(p):
    managed = [".agent/core/autonomy.md", ".agent/core/learning.md"]
    p["files"][".agent/manifest.json"]["managed_inventory"] = capture_managed_inventory(p["files"], managed)
    return p


def test_complete_project_survives_framework_release():
    p = install_inventory(base_project())
    before_role = deepcopy(p["files"][".agent/role/mission.json"])
    before_comp = deepcopy(p["files"][".agent/state/competencies.json"])
    before_rules = deepcopy(p["files"][".agent/knowledge/rules.json"])
    before_registry = deepcopy(p["files"][".agent/integrations/registry.json"])
    status, out, meta = apply_framework_release(
        p, target_version="1.2.0", migrations=migrations(),
        managed_updates={".agent/core/autonomy.md":"# AEF Autonomy v2\n"}
    )
    assert status == "CHANGE"
    assert meta["phase"] == "complete"
    assert out["files"][".agent/role/mission.json"] == before_role
    assert out["files"][".agent/state/competencies.json"] == before_comp
    assert out["files"][".agent/knowledge/rules.json"] == before_rules
    assert out["files"][".agent/integrations/registry.json"] == before_registry


def test_release_replay_is_no_change():
    p = install_inventory(base_project())
    _, once, _ = apply_framework_release(
        p, target_version="1.2.0", migrations=migrations(),
        managed_updates={".agent/core/autonomy.md":"# AEF Autonomy v2\n"}
    )
    status, twice, meta = apply_framework_release(
        once, target_version="1.2.0", migrations=migrations(),
        managed_updates={".agent/core/autonomy.md":"# AEF Autonomy v2\n"}
    )
    assert status == "NO_CHANGE"
    assert twice == once
    assert meta["managed_paths_changed"] == []


def test_local_core_edit_blocks_whole_release_atomically():
    p = install_inventory(base_project())
    p["files"][".agent/core/autonomy.md"] = "LOCAL CUSTOMIZATION"
    before = deepcopy(p)
    status, out, meta = apply_framework_release(
        p, target_version="1.2.0", migrations=migrations(),
        managed_updates={".agent/core/autonomy.md":"# AEF Autonomy v2\n"}
    )
    assert status == "BLOCKED"
    assert meta["phase"] == "managed_files"
    assert meta["reason"] == "managed_file_drift"
    assert out == before
    assert out["files"][".agent/manifest.json"]["schema_version"] == "1.0.0"


def test_project_owned_core_adjacent_file_is_never_overwritten():
    p = install_inventory(base_project())
    p["files"][".agent/core/local-notes.md"] = "mine"
    _, out, _ = apply_framework_release(
        p, target_version="1.1.0", migrations=migrations(),
        managed_updates={".agent/core/autonomy.md":"# AEF Autonomy v2\n"}
    )
    assert out["files"][".agent/core/local-notes.md"] == "mine"


def test_instance_identity_is_stable_across_versions():
    p = install_inventory(base_project())
    instance = p["files"][".agent/manifest.json"]["instance_id"]
    _, out, _ = apply_framework_release(p, target_version="1.2.0", migrations=migrations())
    assert out["files"][".agent/manifest.json"]["instance_id"] == instance


def test_release_without_schema_change_can_update_managed_core_only():
    p = install_inventory(base_project())
    status, out, meta = apply_framework_release(
        p, target_version="1.0.0", migrations=migrations(),
        managed_updates={".agent/core/learning.md":"# AEF Learning clarified\n"}
    )
    assert status == "CHANGE"
    assert meta["applied"] == []
    assert meta["managed_paths_changed"] == [".agent/core/learning.md"]
    assert out["files"][".agent/manifest.json"]["schema_version"] == "1.0.0"
