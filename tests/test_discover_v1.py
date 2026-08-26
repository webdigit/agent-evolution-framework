from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest


import aef.cli as cli
import aef.operations as operations
from aef.filesystem import apply_workspace, load_workspace


REGISTRY_PATH = ".agent/integrations/registry.json"


def initialized_project(registry=None):
    return {
        "files": {
            ".agent/manifest.json": {
                "framework": "aef",
                "framework_version": "1.0.0",
                "schema_version": "1.0.0",
                "instance_id": "agent-1",
                "created_at": "2026-08-14T10:00:00Z",
            },
            ".agent/state/migrations.json": {"applied": []},
            REGISTRY_PATH: deepcopy(registry or {"connectors": []}),
            ".agent/local/preserved.json": {"owned": "locally"},
        },
        "decisions": {"decisions": []},
        "authority": {"grants": ["existing-only"]},
    }


def snapshot():
    return {
        "connectors": [{
            "id": "orbital-db",
            "status": "available",
            "capabilities": [{
                "id": "orbital-db.read",
                "operation": "read",
                "risk": "R0",
                "reversible": True,
                "available": True,
                "native_metadata": {"protocol": "v1"},
            }],
        }],
    }


def test_discover_project_is_deterministic_preserving_and_authority_neutral():
    registry = {
        "extension": {"preserve": True},
        "connectors": [{
            "id": "orbital-db",
            "status": "restricted",
            "owner": "local",
            "capabilities": [{
                "id": "orbital-db.read",
                "operation": "legacy-read",
                "risk": "R4",
                "reversible": False,
                "available": False,
                "hard_approval": True,
                "minimum_level": "L5",
            }],
        }],
    }
    project = initialized_project(registry)
    discovered = snapshot()
    before_project = deepcopy(project)
    before_snapshot = deepcopy(discovered)

    status, out, meta = operations.discover_project(project, discovered)

    assert status == "CHANGE"
    assert project == before_project
    assert discovered == before_snapshot
    assert out["authority"] == before_project["authority"]
    assert out["files"][".agent/local/preserved.json"] == {"owned": "locally"}
    updated = out["files"][REGISTRY_PATH]
    assert updated["extension"] == {"preserve": True}
    connector = updated["connectors"][0]
    assert connector["owner"] == "local"
    capability = connector["capabilities"][0]
    assert capability["operation"] == "read"
    assert capability["available"] is True
    assert capability["native_metadata"] == {"protocol": "v1"}
    assert capability["risk"] == "R4"
    assert capability["hard_approval"] is True
    assert capability["minimum_level"] == "L5"
    assert meta["authority_granted"] is False

    replay_status, replay, replay_meta = operations.discover_project(out, discovered)
    assert replay_status == "NO_CHANGE"
    assert replay == out
    assert replay_meta == meta


def test_discover_marks_missing_inventory_unavailable_without_deleting_it():
    registry = snapshot()
    status, out, _ = operations.discover_project(initialized_project(registry), {"connectors": []})

    assert status == "CHANGE"
    connector = out["files"][REGISTRY_PATH]["connectors"][0]
    assert connector["status"] == "unavailable"
    assert connector["capabilities"][0]["available"] is False


def test_snapshot_cannot_inject_authority_annotations_for_new_capability():
    discovered = snapshot()
    capability = discovered["connectors"][0]["capabilities"][0]
    capability.update({
        "hard_approval": False,
        "minimum_level": "L5",
        "authority": "grant",
        "policy_effect": "allow",
    })

    status, out, meta = operations.discover_project(
        initialized_project(), discovered
    )

    assert status == "CHANGE"
    persisted = out["files"][REGISTRY_PATH]["connectors"][0]["capabilities"][0]
    assert set(persisted) == {
        "id", "operation", "risk", "reversible", "available", "native_metadata",
    }
    assert meta["authority_granted"] is False


@pytest.mark.parametrize("invalid_key", [1, 1.5, True, None, ("tuple",)])
@pytest.mark.parametrize("location", ["root", "metadata", "nested_metadata"])
def test_discovery_rejects_non_text_json_keys_at_every_depth(invalid_key, location):
    discovered = snapshot()
    if location == "root":
        discovered[invalid_key] = "invalid"
    elif location == "metadata":
        discovered["connectors"][0]["capabilities"][0]["native_metadata"] = {
            invalid_key: "invalid"
        }
    else:
        discovered["connectors"][0]["capabilities"][0]["native_metadata"] = {
            "nested": {invalid_key: "invalid"}
        }
    project = initialized_project()
    before_project = deepcopy(project)
    before_snapshot = deepcopy(discovered)

    with pytest.raises(operations.InvalidDiscoverySnapshotError):
        operations.discover_project(project, discovered)

    assert project == before_project
    assert discovered == before_snapshot


@pytest.mark.parametrize("invalid_value", [
    float("nan"), float("inf"), float("-inf"), object(), ("tuple",),
])
def test_discovery_rejects_non_strict_json_values_without_mutation(invalid_value):
    discovered = snapshot()
    discovered["connectors"][0]["capabilities"][0]["native_metadata"] = {
        "value": invalid_value
    }
    project = initialized_project()
    before_project = deepcopy(project)

    with pytest.raises(operations.InvalidDiscoverySnapshotError):
        operations.discover_project(project, discovered)

    assert project == before_project


def test_discovery_output_is_strict_sortable_json_and_filesystem_serializable(tmp_path):
    status, out, _ = operations.discover_project(initialized_project(), snapshot())
    registry = out["files"][REGISTRY_PATH]

    assert status == "CHANGE"
    encoded = json.dumps(registry, sort_keys=True, allow_nan=False)
    assert json.loads(encoded) == registry
    apply_workspace(tmp_path, load_workspace(tmp_path), out)
    assert load_workspace(tmp_path)["files"][REGISTRY_PATH] == registry


@pytest.mark.parametrize("invalid", [
    None,
    [],
    {},
    {"connectors": None},
    {"connectors": [{}]},
    {"connectors": [{"id": "", "status": "available", "capabilities": []}]},
    {"connectors": [
        {"id": "duplicate", "status": "available", "capabilities": []},
        {"id": "duplicate", "status": "available", "capabilities": []},
    ]},
    {"connectors": [{
        "id": "x", "status": "available",
        "capabilities": [{"id": "x.read", "operation": "read", "risk": "R0"}],
    }]},
])
def test_invalid_snapshot_is_rejected_without_mutation(invalid):
    project = initialized_project()
    before = deepcopy(project)

    with pytest.raises(operations.InvalidDiscoverySnapshotError):
        operations.discover_project(project, invalid)

    assert project == before


def test_uninitialized_workspace_is_blocked_without_state_creation():
    project = {"files": {"project.json": {"owned": True}}}
    before = deepcopy(project)

    status, out, meta = operations.discover_project(project, snapshot())

    assert status == "BLOCKED"
    assert out == project == before
    assert meta == {"reason": "workspace_not_initialized", "authority_granted": False}


def initialize_workspace(root):
    project = initialized_project()
    apply_workspace(root, load_workspace(root), project)


def test_cli_discover_dry_run_write_and_replay_json(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot()), encoding="utf-8")
    before = (workspace / REGISTRY_PATH).read_bytes()

    dry_code = cli.main([
        "--json", "--workspace", str(workspace), "discover",
        "--snapshot", str(snapshot_path), "--dry-run",
    ])
    dry = json.loads(capsys.readouterr().out)
    assert dry_code == 0
    assert dry["command"] == "DISCOVER"
    assert dry["status"] == "CHANGE"
    assert dry["dry_run"] is True
    assert dry["result"]["authority_granted"] is False
    assert (workspace / REGISTRY_PATH).read_bytes() == before

    write_code = cli.main([
        "--json", "--workspace", str(workspace), "discover",
        "--snapshot", str(snapshot_path),
    ])
    written = json.loads(capsys.readouterr().out)
    replay_code = cli.main([
        "--json", "--workspace", str(workspace), "discover",
        "--snapshot", str(snapshot_path),
    ])
    replay = json.loads(capsys.readouterr().out)

    assert write_code == replay_code == 0
    assert written["status"] == "CHANGE"
    assert replay["status"] == "NO_CHANGE"
    assert replay["diff"] == {"created": [], "modified": [], "removed": []}
    registry = load_workspace(workspace)["files"][REGISTRY_PATH]
    assert registry["connectors"][0]["id"] == "orbital-db"


def test_cli_discover_human_output_and_blocked_code(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot()), encoding="utf-8")

    code = cli.main([
        "--human", "--workspace", str(workspace), "discover",
        "--snapshot", str(snapshot_path), "--dry-run",
    ])
    output = capsys.readouterr().out

    assert code == 0
    assert output.startswith("[OK] Connector discovery would update the registry\n")
    assert "Connectors  : 1" in output
    assert "Capabilities: 1" in output
    assert "Authority   : unchanged" in output
    assert '"api_version"' not in output

    blocked = cli.main([
        "--human", "--workspace", str(tmp_path / "not-initialized"), "discover",
        "--snapshot", str(snapshot_path),
    ])
    blocked_output = capsys.readouterr().out
    assert blocked == 4
    assert blocked_output.startswith("[BLOCKED] Connector discovery requires an initialized AEF workspace")


def test_discover_subprocess_non_tty_is_json_and_invalid_input_is_code_three(tmp_path):
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    good = tmp_path / "snapshot.json"
    bad = tmp_path / "bad.json"
    good.write_text(json.dumps(snapshot()), encoding="utf-8")
    bad.write_text("{invalid", encoding="utf-8")
    python = Path(sys.executable)
    prefix = [str(python), "-m", "aef"]

    completed = subprocess.run(
        [*prefix, "--workspace", str(workspace), "discover", "--snapshot", str(good)],
        input="", capture_output=True, text=True, check=False,
    )
    invalid = subprocess.run(
        [*prefix, "--json", "--workspace", str(workspace), "discover", "--snapshot", str(bad)],
        input="", capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["command"] == "DISCOVER"
    assert "[OK]" not in completed.stdout
    assert invalid.returncode == 3
    assert json.loads(invalid.stdout)["error"]["code"] == "invalid_json"
    assert "Traceback" not in completed.stderr + invalid.stderr


def test_snapshot_and_persisted_registry_have_distinct_domain_errors():
    malformed = {"connectors": None}
    project = initialized_project()
    project["files"][REGISTRY_PATH] = deepcopy(malformed)

    with pytest.raises(operations.InvalidDiscoverySnapshotError):
        operations.discover_project(initialized_project(), malformed)
    with pytest.raises(operations.InvalidDiscoveryRegistryError):
        operations.discover_project(project, snapshot())


@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_invalid_persisted_registry_is_publicly_classified_without_leaks(tmp_path, mode):
    workspace = tmp_path / "workspace"
    initialize_workspace(workspace)
    registry_path = workspace / REGISTRY_PATH
    original = '{"connectors":null,"private_extension":"DO_NOT_LEAK"}\n'
    registry_path.write_text(original, encoding="utf-8")
    supplied = tmp_path / "snapshot.json"
    supplied.write_text(json.dumps(snapshot()), encoding="utf-8")

    python = Path(sys.executable)
    prefix = [str(python), "-m", "aef"]
    output_option = "--human" if mode == "human" else f"--{mode}"
    completed = subprocess.run(
        [
            *prefix, output_option, "--workspace", str(workspace), "discover",
            "--snapshot", str(supplied),
        ],
        input="", capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 3
    assert registry_path.read_text(encoding="utf-8") == original
    assert "Traceback" not in completed.stdout + completed.stderr
    assert "DO_NOT_LEAK" not in completed.stdout + completed.stderr
    assert str(workspace) not in completed.stderr
    if mode == "human":
        assert completed.stdout == (
            "[ERROR] The persisted connector registry is invalid.\n\n"
            "Code      : invalid_discovery_registry\n"
        )
        assert "{" not in completed.stdout
    else:
        envelope = json.loads(completed.stdout)
        assert envelope["error"] == {
            "code": "invalid_discovery_registry",
            "message": "The persisted connector registry is invalid.",
            "details": {},
        }
        assert str(workspace) not in envelope["error"]["message"]
        if mode == "compact":
            assert completed.stdout.count("\n") == 1
    assert completed.stderr == (
        "aef: invalid_discovery_registry: "
        "The persisted connector registry is invalid.\n"
    )
