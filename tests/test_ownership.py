from aef.ownership import capture_managed_inventory, detect_managed_drift, reconcile_framework_file


def test_capture_inventory_is_deterministic():
    files = {"a":"A","b":{"x":1}}
    assert capture_managed_inventory(files,["b","a"]) == capture_managed_inventory(files,["a","b"])


def test_project_owned_unknown_file_is_not_in_managed_inventory():
    files = {".agent/core/a":"A","notes.txt":"mine"}
    inventory = capture_managed_inventory(files,[".agent/core/a"])
    assert "notes.txt" not in inventory


def test_locally_modified_framework_file_is_detected():
    files = {".agent/core/a":"A"}
    inventory = capture_managed_inventory(files,[".agent/core/a"])
    files[".agent/core/a"] = "LOCAL EDIT"
    drift = detect_managed_drift(files, inventory)
    assert drift[0]["status"] == "locally_modified"


def test_framework_upgrade_does_not_clobber_local_edit():
    files = {".agent/core/a":"A"}
    inventory = capture_managed_inventory(files,[".agent/core/a"])
    files[".agent/core/a"] = "LOCAL EDIT"
    status, out, inv, finding = reconcile_framework_file(files, inventory, path=".agent/core/a", desired_content="B")
    assert status == "BLOCKED"
    assert out[".agent/core/a"] == "LOCAL EDIT"
    assert finding["status"] == "locally_modified"


def test_unchanged_framework_file_can_upgrade_and_replay():
    files = {".agent/core/a":"A"}
    inventory = capture_managed_inventory(files,[".agent/core/a"])
    status, files2, inv2, _ = reconcile_framework_file(files, inventory, path=".agent/core/a", desired_content="B")
    assert status == "CHANGE"
    assert files2[".agent/core/a"] == "B"
    status, files3, inv3, _ = reconcile_framework_file(files2, inv2, path=".agent/core/a", desired_content="B")
    assert status == "NO_CHANGE"
    assert files3 == files2
    assert inv3 == inv2
