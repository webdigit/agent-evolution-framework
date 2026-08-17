import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import installed_aef_script

import aef.filesystem as filesystem
from aef.filesystem import (
    EVALUATION_TRANSACTION_PATH,
    EvaluationRecoveryRequiredError,
    WorkspacePathError,
    apply_workspace,
    load_workspace,
)


def _desired(path: str, value="content"):
    return {"files": {path: value}}


def test_normal_agent_file_path_is_allowed(tmp_path: Path):
    diff = apply_workspace(
        tmp_path,
        load_workspace(tmp_path),
        _desired(".agent/state/example.json", {"ok": True}),
    )

    assert diff["created"] == [".agent/state/example.json"]
    assert (tmp_path / ".agent/state/example.json").is_file()


@pytest.mark.parametrize("unsafe_path", [
    ".agent/../outside.txt",
    "/tmp/aef-outside.txt",
    "C:/aef/outside.txt",
    "C:aef/outside.txt",
    "//server/share/outside.txt",
    r"\\server\share\outside.txt",
    r".agent\..\outside.txt",
    r".agent\state\example.json",
    ".agent//state/example.json",
    ".agent/state/value.txt:stream",
    ".agent/CON",
    ".agent/state/prn.json",
    ".agent/state/AUX.txt",
    ".agent/state/NUL.tar.gz",
    ".agent/state/COM1.log",
    ".agent/state/com9",
    ".agent/state/LPT1.txt",
    ".agent/state/lpt9",
    ".agent/state/trailing.",
    ".agent/state/trailing ",
])
def test_unsafe_workspace_paths_are_rejected(tmp_path: Path, unsafe_path: str):
    with pytest.raises(WorkspacePathError):
        apply_workspace(tmp_path, load_workspace(tmp_path), _desired(unsafe_path))

    assert not (tmp_path / ".agent").exists()


def test_mixed_valid_and_invalid_plan_writes_nothing(tmp_path: Path):
    desired = {
        "files": {
            ".agent/state/valid.json": {"would": "be partial"},
            "../outside.json": {"forbidden": True},
        }
    }

    with pytest.raises(WorkspacePathError):
        apply_workspace(tmp_path, load_workspace(tmp_path), desired)

    assert not (tmp_path / ".agent/state/valid.json").exists()
    assert not (tmp_path.parent / "outside.json").exists()


def test_unserializable_content_blocks_complete_plan_before_mkdir(tmp_path: Path):
    desired = {
        "files": {
            ".agent/a-valid.json": {"serializable": True},
            ".agent/z-invalid.json": {"value": object()},
        }
    }

    with pytest.raises(TypeError):
        apply_workspace(tmp_path, load_workspace(tmp_path), desired)

    assert not (tmp_path / ".agent").exists()


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_json_blocks_multi_file_plan_before_first_write(tmp_path: Path, non_finite):
    desired = {
        "files": {
            ".agent/a-valid.json": {"serializable": True},
            ".agent/z-invalid.json": {"xp": non_finite},
        }
    }

    with pytest.raises(ValueError, match="Out of range float values"):
        apply_workspace(tmp_path, load_workspace(tmp_path), desired)

    assert not (tmp_path / ".agent").exists()


class _CustomJSONKey:
    pass


@pytest.mark.parametrize(
    ("invalid_key", "nested"),
    [
        pytest.param(1, False, id="integer-root"),
        pytest.param(("tuple",), False, id="tuple-root"),
        pytest.param(True, True, id="boolean-nested"),
        pytest.param(_CustomJSONKey(), True, id="custom-nested"),
    ],
)
def test_non_text_json_key_is_type_error_before_first_write(
    tmp_path: Path, invalid_key, nested
):
    invalid = {invalid_key: "not coerced"}
    document = {"nested": invalid} if nested else invalid
    desired = {
        "files": {
            ".agent/a-valid.json": {"serializable": True},
            ".agent/z-invalid.json": document,
        }
    }

    with pytest.raises(TypeError, match="JSON object keys must be strings"):
        apply_workspace(tmp_path, load_workspace(tmp_path), desired)

    assert desired["files"][".agent/z-invalid.json"] is document
    assert invalid_key in invalid
    assert not (tmp_path / ".agent").exists()


def test_cyclic_json_is_value_error_without_mutation_or_write(tmp_path: Path):
    cycle = []
    cycle.append(cycle)
    desired = {
        "files": {
            ".agent/a-valid.json": {"serializable": True},
            ".agent/z-invalid.json": {"cycle": cycle},
        }
    }

    with pytest.raises(ValueError, match="Circular reference detected"):
        apply_workspace(tmp_path, load_workspace(tmp_path), desired)

    assert len(cycle) == 1 and cycle[0] is cycle
    assert not (tmp_path / ".agent").exists()


def test_existing_symlink_in_agent_path_is_rejected(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    agent = tmp_path / ".agent"
    agent.mkdir()
    link = agent / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(WorkspacePathError):
        apply_workspace(
            tmp_path,
            load_workspace(tmp_path),
            _desired(".agent/linked/escaped.json", {"forbidden": True}),
        )

    assert not (outside / "escaped.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_existing_windows_junction_in_agent_path_is_rejected(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    agent = tmp_path / ".agent"
    agent.mkdir()
    junction = agent / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junctions are unavailable: {result.stderr or result.stdout}")

    with pytest.raises(WorkspacePathError):
        apply_workspace(
            tmp_path,
            load_workspace(tmp_path),
            _desired(".agent/junction/escaped.json", {"forbidden": True}),
        )

    assert not (outside / "escaped.json").exists()


def test_file_replacement_uses_temporary_sibling_and_os_replace(tmp_path: Path, monkeypatch):
    target = tmp_path / ".agent/state/value.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"value":"old"}\n', encoding="utf-8")
    observed = []
    real_replace = filesystem.os.replace

    def observe_replace(source, destination):
        observed.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(filesystem.os, "replace", observe_replace)
    current = load_workspace(tmp_path)
    diff = apply_workspace(
        tmp_path,
        current,
        {"files": {".agent/state/value.json": {"value": "new"}}},
    )

    assert diff["modified"] == [".agent/state/value.json"]
    assert len(observed) == 1
    temporary, destination = observed[0]
    assert temporary.parent == target.parent
    assert destination == target
    assert load_workspace(tmp_path)["files"][".agent/state/value.json"] == {"value": "new"}


def test_failed_replace_cleans_temporary_and_preserves_previous_content(tmp_path: Path, monkeypatch):
    target = tmp_path / ".agent/state/value.txt"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    current = load_workspace(tmp_path)

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(filesystem.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        apply_workspace(
            tmp_path,
            current,
            {"files": {".agent/state/value.txt": "new"}},
        )

    assert target.read_text(encoding="utf-8") == "old"
    assert list(target.parent.glob(".value.txt.*.tmp")) == []


def test_failure_before_replace_cleans_temporary_and_preserves_target(tmp_path: Path, monkeypatch):
    target = tmp_path / ".agent/state/value.txt"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")
    current = load_workspace(tmp_path)

    def fail_sync(file_descriptor):
        raise OSError("simulated sync failure")

    monkeypatch.setattr(filesystem.os, "fsync", fail_sync)

    with pytest.raises(OSError, match="simulated sync failure"):
        apply_workspace(
            tmp_path,
            current,
            {"files": {".agent/state/value.txt": "new"}},
        )

    assert target.read_text(encoding="utf-8") == "old"
    assert list(target.parent.glob(".value.txt.*.tmp")) == []


def test_no_change_does_not_rewrite_file(tmp_path: Path, monkeypatch):
    target = tmp_path / ".agent/state/value.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"value":"same"}', encoding="utf-8")
    current = load_workspace(tmp_path)

    def unexpected_write(root, rel_path, target, content):
        raise AssertionError("NO_CHANGE must not write")

    monkeypatch.setattr(filesystem, "_atomic_write", unexpected_write)
    diff = apply_workspace(tmp_path, current, current)

    assert diff == {"created": [], "modified": [], "removed": []}
    assert target.read_bytes() == b'{"value":"same"}'


def test_revalidation_failure_before_staging_creates_no_target_or_temporary(tmp_path: Path, monkeypatch):
    calls = 0
    real_validate = filesystem._validate_workspace_path

    def fail_before_staging(root, rel_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkspacePathError("simulated change before staging")
        return real_validate(root, rel_path)

    monkeypatch.setattr(filesystem, "_validate_workspace_path", fail_before_staging)
    target = tmp_path / ".agent/state/value.json"

    with pytest.raises(WorkspacePathError, match="before staging"):
        apply_workspace(
            tmp_path,
            load_workspace(tmp_path),
            {"files": {".agent/state/value.json": {"value": "new"}}},
        )

    assert not target.exists()
    assert list(target.parent.glob(".value.json.*.tmp")) == []


def test_revalidation_failure_before_replace_preserves_target_and_cleans_temporary(tmp_path: Path, monkeypatch):
    target = tmp_path / ".agent/state/value.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"value":"old"}\n', encoding="utf-8")
    current = load_workspace(tmp_path)
    calls = 0
    real_validate = filesystem._validate_workspace_path

    def fail_before_replace(root, rel_path):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise WorkspacePathError("simulated change before replacement")
        return real_validate(root, rel_path)

    monkeypatch.setattr(filesystem, "_validate_workspace_path", fail_before_replace)

    with pytest.raises(WorkspacePathError, match="before replacement"):
        apply_workspace(
            tmp_path,
            current,
            {"files": {".agent/state/value.json": {"value": "new"}}},
        )

    assert target.read_text(encoding="utf-8") == '{"value":"old"}\n'
    assert list(target.parent.glob(".value.json.*.tmp")) == []


def test_parent_directory_close_failure_is_best_effort(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(filesystem.os, "name", "posix")
    monkeypatch.setattr(filesystem.os, "open", lambda path, flags: 42)
    monkeypatch.setattr(filesystem.os, "fsync", lambda descriptor: None)

    def fail_close(descriptor):
        raise OSError("simulated directory close failure")

    monkeypatch.setattr(filesystem.os, "close", fail_close)

    filesystem._sync_parent_directory(tmp_path)


def test_atomic_update_preserves_unknown_agent_files(tmp_path: Path):
    unknown = tmp_path / ".agent/local/unknown.txt"
    target = tmp_path / ".agent/state/value.txt"
    unknown.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    unknown.write_text("preserve me", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    current = load_workspace(tmp_path)
    desired = load_workspace(tmp_path)
    desired["files"][".agent/state/value.txt"] = "new"

    apply_workspace(tmp_path, current, desired)

    assert unknown.read_text(encoding="utf-8") == "preserve me"
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("target", [
    ".agent/manifest.json",
    ".agent/knowledge/knowledge.json",
    ".agent/integrations/registry.json",
    ".agent/state/career.json",
    ".agent/state/competencies.json",
    ".agent/state/evaluations.json",
])
def test_disk_transaction_blocks_public_mutation_with_stale_current(
    tmp_path: Path, target: str
):
    stale = load_workspace(tmp_path)
    journal_state = load_workspace(tmp_path)
    journal_state["files"][EVALUATION_TRANSACTION_PATH] = {"present": True}
    apply_workspace(tmp_path, stale, journal_state)
    desired = {"files": {target: {"changed": True}}}

    with pytest.raises(
        EvaluationRecoveryRequiredError,
        match="evaluation recovery is required",
    ):
        apply_workspace(tmp_path, stale, desired)

    assert not (tmp_path / target).exists()


def test_current_transaction_blocks_public_mutation_when_disk_journal_is_absent(
    tmp_path: Path,
):
    current = load_workspace(tmp_path)
    current["files"][EVALUATION_TRANSACTION_PATH] = {"present": True}
    desired = {"files": {
        **current["files"], ".agent/state/career.json": {"level": "L2"},
    }}

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(tmp_path, current, desired)

    assert not (tmp_path / ".agent/state/career.json").exists()


def test_transaction_in_current_and_on_disk_blocks_before_any_partial_write(tmp_path: Path):
    current = load_workspace(tmp_path)
    desired = {"files": {EVALUATION_TRANSACTION_PATH: {"present": True}}}
    apply_workspace(tmp_path, current, desired)
    current = load_workspace(tmp_path)
    attempted = {"files": {
        **current["files"],
        ".agent/state/career.json": {"level": "L2"},
        ".agent/state/evaluations.json": {"history": []},
    }}

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(tmp_path, current, attempted)

    assert not (tmp_path / ".agent/state/career.json").exists()
    assert not (tmp_path / ".agent/state/evaluations.json").exists()


def test_transaction_guard_allows_strict_no_change_plan(tmp_path: Path):
    current = load_workspace(tmp_path)
    desired = {"files": {EVALUATION_TRANSACTION_PATH: {"present": True}}}
    apply_workspace(tmp_path, current, desired)
    current = load_workspace(tmp_path)

    diff = apply_workspace(tmp_path, current, current)

    assert diff == {"created": [], "modified": [], "removed": []}


@pytest.mark.parametrize("entry_kind", ["empty", "invalid", "directory"])
def test_reserved_transaction_entry_blocks_regardless_of_content_or_type(
    tmp_path: Path, entry_kind: str
):
    reserved = tmp_path / EVALUATION_TRANSACTION_PATH
    reserved.parent.mkdir(parents=True)
    if entry_kind == "empty":
        reserved.write_bytes(b"")
    elif entry_kind == "invalid":
        reserved.write_text("not json", encoding="utf-8")
    else:
        reserved.mkdir()
    current = load_workspace(tmp_path)
    desired = {"files": {
        **current["files"], ".agent/state/career.json": {"level": "L2"},
    }}

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(tmp_path, current, desired)

    assert not (tmp_path / ".agent/state/career.json").exists()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("broken", [False, True])
def test_reserved_transaction_symlink_blocks_even_when_broken(tmp_path: Path, broken: bool):
    reserved = tmp_path / EVALUATION_TRANSACTION_PATH
    reserved.parent.mkdir(parents=True)
    target = tmp_path / ("missing-target" if broken else "journal-target")
    if not broken:
        target.write_text("{}", encoding="utf-8")
    try:
        reserved.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")
    current = {"files": {}}
    desired = {"files": {".agent/state/career.json": {"level": "L2"}}}

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(tmp_path, current, desired)

    assert not (tmp_path / ".agent/state/career.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_reserved_transaction_junction_blocks(tmp_path: Path):
    reserved = tmp_path / EVALUATION_TRANSACTION_PATH
    reserved.parent.mkdir(parents=True)
    target = tmp_path / "junction-target"
    target.mkdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(reserved), str(target)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junctions are unavailable: {result.stderr or result.stdout}")

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(
            tmp_path, {"files": {}},
            {"files": {".agent/state/career.json": {"level": "L2"}}},
        )


@pytest.mark.parametrize("failure", [PermissionError("denied"), OSError("indeterminate")])
def test_transaction_entry_inspection_errors_fail_closed(
    monkeypatch, tmp_path: Path, failure: OSError
):
    real_lstat = filesystem.os.lstat

    def fail_reserved(path):
        if str(path).replace("\\", "/").endswith(EVALUATION_TRANSACTION_PATH):
            raise failure
        return real_lstat(path)

    monkeypatch.setattr(filesystem.os, "lstat", fail_reserved)

    with pytest.raises(EvaluationRecoveryRequiredError) as caught:
        apply_workspace(
            tmp_path, {"files": {}},
            {"files": {".agent/state/career.json": {"level": "L2"}}},
        )

    assert str(tmp_path) not in str(caught.value)
    assert not (tmp_path / ".agent").exists()


def test_transaction_entry_appearing_between_inspections_blocks_before_write(
    monkeypatch, tmp_path: Path
):
    calls = 0
    real_lstat = filesystem.os.lstat

    def changing_lstat(path):
        nonlocal calls
        if not str(path).replace("\\", "/").endswith(EVALUATION_TRANSACTION_PATH):
            return real_lstat(path)
        calls += 1
        if calls == 1:
            raise FileNotFoundError
        return real_lstat(tmp_path)

    monkeypatch.setattr(filesystem.os, "lstat", changing_lstat)

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(
            tmp_path, {"files": {}},
            {"files": {".agent/state/career.json": {"level": "L2"}}},
        )

    assert calls == 2
    assert not (tmp_path / ".agent").exists()


def test_transaction_entry_disappearing_after_inspection_still_blocks(
    monkeypatch, tmp_path: Path
):
    reserved = tmp_path / EVALUATION_TRANSACTION_PATH
    reserved.parent.mkdir(parents=True)
    reserved.write_text("{}", encoding="utf-8")
    real_lstat = filesystem.os.lstat

    def disappearing_lstat(path):
        metadata = real_lstat(path)
        if str(path).replace("\\", "/").endswith(EVALUATION_TRANSACTION_PATH):
            reserved.unlink(missing_ok=True)
        return metadata

    monkeypatch.setattr(filesystem.os, "lstat", disappearing_lstat)

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_workspace(
            tmp_path, {"files": {}},
            {"files": {".agent/state/career.json": {"level": "L2"}}},
        )

    assert not (tmp_path / ".agent/state/career.json").exists()


@pytest.mark.parametrize("launcher", ["module", "script"])
@pytest.mark.parametrize("mode", ["human", "json", "compact"])
def test_fail_closed_transaction_entry_has_stable_cli_output(
    tmp_path: Path, launcher: str, mode: str
):
    reserved = tmp_path / EVALUATION_TRANSACTION_PATH
    reserved.parent.mkdir(parents=True)
    reserved.mkdir()
    executable = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(executable), "-m", "aef"] if launcher == "module" else [str(script)]
    option = "--human" if mode == "human" else f"--{mode}"

    completed = subprocess.run(
        [*prefix, option, "--workspace", str(tmp_path), "init", "--role", "generalist-agent"],
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 4
    assert "Traceback" not in completed.stdout + completed.stderr
    assert str(tmp_path) not in completed.stderr
    assert not (tmp_path / ".agent/manifest.json").exists()
    assert not list(tmp_path.rglob("*.tmp"))
    if mode == "human":
        assert "Evaluation recovery is required before workspace mutation." in completed.stdout
    else:
        envelope = json.loads(completed.stdout)
        assert envelope["error"]["code"] == "evaluation_recovery_required"
        if mode == "compact":
            assert completed.stdout.count("\n") == 1
