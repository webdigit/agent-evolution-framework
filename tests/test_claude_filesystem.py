import os
from pathlib import Path

import pytest

from aef.claude_filesystem import (
    ClaudeIntegrationFilesystemError, apply_claude_bridge,
    read_claude_bridge, validate_claude_doctrine_files,
)
from aef.claude_integration import CLAUDE_BRIDGE_BYTES, CORE_DOCTRINE_PATHS
from aef.filesystem import EvaluationRecoveryRequiredError


def workspace(tmp_path):
    for relative in CORE_DOCTRINE_PATHS:
        target = tmp_path.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Doctrine\n", encoding="utf-8")
    return tmp_path


def test_bridge_write_is_exact_atomic_and_replay_safe(tmp_path):
    root = workspace(tmp_path)
    expected = b"# User\r\n\n\n" + CLAUDE_BRIDGE_BYTES

    diff = apply_claude_bridge(root, None, expected)
    target = root / ".claude/CLAUDE.md"
    first_mtime = target.stat().st_mtime_ns

    assert diff == {"created": [".claude/CLAUDE.md"], "modified": [], "removed": []}
    assert target.read_bytes() == expected
    assert apply_claude_bridge(root, expected, expected) == {
        "created": [], "modified": [], "removed": [],
    }
    assert target.stat().st_mtime_ns == first_mtime
    assert not list(target.parent.glob("*.tmp"))


def test_empty_remove_result_keeps_the_file(tmp_path):
    root = workspace(tmp_path)
    apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    apply_claude_bridge(root, CLAUDE_BRIDGE_BYTES, b"")
    target = root / ".claude/CLAUDE.md"
    assert target.is_file()
    assert target.read_bytes() == b""


def test_invalid_utf8_existing_bridge_is_rejected_without_rewrite(tmp_path):
    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    target.parent.mkdir()
    target.write_bytes(b"\xff")
    with pytest.raises(ClaudeIntegrationFilesystemError):
        read_claude_bridge(root)
    assert target.read_bytes() == b"\xff"


def test_missing_or_invalid_doctrine_is_rejected(tmp_path):
    root = workspace(tmp_path)
    target = root.joinpath(*CORE_DOCTRINE_PATHS[0].split("/"))
    target.write_bytes(b"\xff")
    with pytest.raises(ClaudeIntegrationFilesystemError):
        validate_claude_doctrine_files(root)
    assert not (root / ".claude").exists()


def test_preflight_change_is_rejected_before_replace(tmp_path, monkeypatch):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    target.parent.mkdir()
    target.write_bytes(b"before")
    calls = 0
    original = filesystem.read_claude_bridge

    def changed(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_bytes(b"foreign")
        return original(path)

    monkeypatch.setattr(filesystem, "read_claude_bridge", changed)
    with pytest.raises(ClaudeIntegrationFilesystemError):
        apply_claude_bridge(root, b"before", b"after")
    assert target.read_bytes() == b"foreign"
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinked_claude_directory_is_rejected(tmp_path):
    root = workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, root / ".claude", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ClaudeIntegrationFilesystemError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert not (outside / "CLAUDE.md").exists()


def test_parent_and_user_files_are_never_touched(tmp_path):
    parent_claude = tmp_path / "CLAUDE.md"
    user_settings = tmp_path / "fake-home/.claude/settings.json"
    parent_claude.write_bytes(b"parent")
    user_settings.parent.mkdir(parents=True)
    user_settings.write_bytes(b"user")
    root = workspace(tmp_path / "project")
    apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert parent_claude.read_bytes() == b"parent"
    assert user_settings.read_bytes() == b"user"


@pytest.mark.parametrize("entry_kind", ["file", "empty", "invalid", "directory"])
def test_direct_adapter_blocks_every_reserved_transaction_entry(tmp_path, entry_kind):
    root = workspace(tmp_path)
    transaction = root / ".agent/state/evaluation-transaction.json"
    transaction.parent.mkdir(parents=True, exist_ok=True)
    if entry_kind == "directory":
        transaction.mkdir()
    else:
        content = {"file": b"{}\n", "empty": b"", "invalid": b"not json"}[entry_kind]
        transaction.write_bytes(content)

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)

    assert not (root / ".claude").exists()


def test_direct_adapter_blocks_stale_snapshot_when_real_journal_exists(tmp_path):
    root = workspace(tmp_path)
    transaction = root / ".agent/state/evaluation-transaction.json"
    transaction.parent.mkdir(parents=True, exist_ok=True)
    transaction.write_bytes(b"{}\n")

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)

    assert not (root / ".claude").exists()


def test_direct_adapter_no_change_does_not_write_during_recovery(tmp_path):
    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    target.parent.mkdir()
    target.write_bytes(CLAUDE_BRIDGE_BYTES)
    transaction = root / ".agent/state/evaluation-transaction.json"
    transaction.parent.mkdir(parents=True, exist_ok=True)
    transaction.write_bytes(b"{}\n")
    before = target.stat().st_mtime_ns

    assert apply_claude_bridge(
        root, CLAUDE_BRIDGE_BYTES, CLAUDE_BRIDGE_BYTES
    ) == {"created": [], "modified": [], "removed": []}
    assert target.stat().st_mtime_ns == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
@pytest.mark.parametrize("broken", [False, True])
def test_direct_adapter_blocks_transaction_symlink(tmp_path, broken):
    root = workspace(tmp_path)
    state = root / ".agent/state"
    state.mkdir(parents=True, exist_ok=True)
    destination = root / "journal-target.json"
    if not broken:
        destination.write_bytes(b"{}\n")
    try:
        os.symlink(destination, state / "evaluation-transaction.json")
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)

    assert not (root / ".claude").exists()


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("unknown")])
def test_direct_adapter_blocks_indeterminate_journal_inspection(
    tmp_path, monkeypatch, error,
):
    import aef.filesystem as filesystem

    root = workspace(tmp_path)
    original = filesystem.os.lstat
    reserved = root / ".agent/state/evaluation-transaction.json"

    def fail_reserved(path):
        if Path(path) == reserved:
            raise error
        return original(path)

    monkeypatch.setattr(filesystem.os, "lstat", fail_reserved)
    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert not (root / ".claude").exists()


def test_failed_claude_directory_creation_leaves_no_staging_directory(
    tmp_path, monkeypatch,
):
    root = workspace(tmp_path)
    original = Path.mkdir

    def fail_claude(path, *args, **kwargs):
        if path == root / ".claude":
            raise OSError("mkdir failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_claude)
    with pytest.raises(ClaudeIntegrationFilesystemError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert not (root / ".claude").exists()


def test_failed_mkstemp_removes_directory_created_by_this_call(tmp_path, monkeypatch):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    monkeypatch.setattr(
        filesystem.tempfile, "mkstemp",
        lambda **kwargs: (_ for _ in ()).throw(OSError("staging failed")),
    )
    with pytest.raises(ClaudeIntegrationFilesystemError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert not (root / ".claude").exists()


class _FailingStream:
    def __init__(self, stream, phase):
        self._stream = stream
        self._phase = phase

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stream.close()

    def fileno(self):
        return self._stream.fileno()

    def write(self, value):
        if self._phase == "write":
            raise OSError("write failed")
        return self._stream.write(value)

    def flush(self):
        if self._phase == "flush":
            raise OSError("flush failed")
        return self._stream.flush()

    def close(self):
        return self._stream.close()


@pytest.mark.parametrize("phase", ["open", "write", "flush", "replace"])
@pytest.mark.parametrize("existing_target", [False, True])
def test_failed_staging_preserves_existing_content_and_owned_directory(
    tmp_path, monkeypatch, phase, existing_target,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    existing = None
    if existing_target:
        target.parent.mkdir()
        target.write_bytes(b"before")
        existing = b"before"
    original_fdopen = filesystem.os.fdopen

    if phase == "open":
        monkeypatch.setattr(
            filesystem.os, "fdopen",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("open failed")),
        )
    elif phase in {"write", "flush"}:
        monkeypatch.setattr(
            filesystem.os, "fdopen",
            lambda *args, **kwargs: _FailingStream(
                original_fdopen(*args, **kwargs), phase
            ),
        )
    elif phase == "replace":
        monkeypatch.setattr(
            filesystem.os, "replace",
            lambda *args: (_ for _ in ()).throw(OSError("replace failed")),
        )

    with pytest.raises(ClaudeIntegrationFilesystemError):
        apply_claude_bridge(root, existing, b"after")

    if existing_target:
        assert target.read_bytes() == b"before"
        assert target.parent.is_dir()
    else:
        assert not target.parent.exists()
    assert not list(root.rglob(".aef-claude-*.tmp"))


def test_failed_staging_never_removes_content_that_appears_in_new_directory(
    tmp_path, monkeypatch,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)

    def fail_after_foreign_content(**kwargs):
        (root / ".claude/user.txt").write_bytes(b"user")
        raise OSError("staging failed")

    monkeypatch.setattr(filesystem.tempfile, "mkstemp", fail_after_foreign_content)
    with pytest.raises(ClaudeIntegrationFilesystemError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert (root / ".claude/user.txt").read_bytes() == b"user"


@pytest.mark.parametrize("existing_target", [False, True])
def test_file_fsync_failure_is_visible_and_never_publishes_target(
    tmp_path, monkeypatch, existing_target,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    existing = None
    if existing_target:
        target.parent.mkdir()
        target.write_bytes(b"before")
        existing = b"before"
    monkeypatch.setattr(
        filesystem.os, "fsync",
        lambda descriptor: (_ for _ in ()).throw(OSError("file fsync failed")),
    )

    with pytest.raises(ClaudeIntegrationFilesystemError) as raised:
        apply_claude_bridge(root, existing, b"after")

    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "file fsync failed"
    if existing_target:
        assert target.read_bytes() == b"before"
    else:
        assert not target.exists()
        assert not target.parent.exists()
    assert not list(root.rglob(".aef-claude-*.tmp"))


def test_close_failure_does_not_mask_primary_write_error(tmp_path, monkeypatch):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    original_fdopen = filesystem.os.fdopen

    class WriteAndCloseFailure(_FailingStream):
        def __init__(self, stream):
            super().__init__(stream, "write")

        def close(self):
            self._stream.close()
            raise OSError("close failed")

    monkeypatch.setattr(
        filesystem.os, "fdopen",
        lambda *args, **kwargs: WriteAndCloseFailure(
            original_fdopen(*args, **kwargs)
        ),
    )

    with pytest.raises(ClaudeIntegrationFilesystemError) as raised:
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)

    assert str(raised.value.__cause__) == "write failed"
    assert not (root / ".claude").exists()
    assert not list(root.rglob(".aef-claude-*.tmp"))


def _forbid_claude_publication(monkeypatch):
    import aef.claude_filesystem as filesystem

    calls = {"mkstemp": 0, "fdopen": 0, "replace": 0}

    def forbidden(name):
        def fail(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f"{name} called after recovery was detected")
        return fail

    monkeypatch.setattr(filesystem.tempfile, "mkstemp", forbidden("mkstemp"))
    monkeypatch.setattr(filesystem.os, "fdopen", forbidden("fdopen"))
    monkeypatch.setattr(filesystem.os, "replace", forbidden("replace"))
    return calls


def _create_transaction(root):
    transaction = root / ".agent/state/evaluation-transaction.json"
    transaction.parent.mkdir(parents=True, exist_ok=True)
    transaction.write_bytes(b"{}\n")
    return transaction


def test_late_journal_during_parent_revalidation_blocks_before_staging(
    tmp_path, monkeypatch,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    original = filesystem._is_link_or_reparse
    appeared = False

    def appear_during_parent(path):
        nonlocal appeared
        result = original(path)
        if path == root / ".claude" and not appeared:
            _create_transaction(root)
            appeared = True
        return result

    monkeypatch.setattr(filesystem, "_is_link_or_reparse", appear_during_parent)
    calls = _forbid_claude_publication(monkeypatch)
    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert calls == {"mkstemp": 0, "fdopen": 0, "replace": 0}
    assert not (root / ".claude").exists()


def test_late_journal_during_bridge_revalidation_preserves_old_target(
    tmp_path, monkeypatch,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    target.parent.mkdir()
    target.write_bytes(b"before")
    original = filesystem.read_claude_bridge
    reads = 0

    def appear_during_bridge(path):
        nonlocal reads
        value = original(path)
        reads += 1
        if reads == 2:
            _create_transaction(root)
        return value

    monkeypatch.setattr(filesystem, "read_claude_bridge", appear_during_bridge)
    calls = _forbid_claude_publication(monkeypatch)
    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, b"before", b"after")
    assert calls == {"mkstemp": 0, "fdopen": 0, "replace": 0}
    assert target.read_bytes() == b"before"


def test_late_journal_immediately_before_mkdir_blocks_without_directory(
    tmp_path, monkeypatch,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    original = filesystem._evaluation_transaction_entry_present
    inspections = 0

    def appear_after_bridge_guard(path):
        nonlocal inspections
        inspections += 1
        present = original(path)
        if inspections == 3:
            _create_transaction(root)
        return present

    monkeypatch.setattr(
        filesystem, "_evaluation_transaction_entry_present",
        appear_after_bridge_guard,
    )
    calls = _forbid_claude_publication(monkeypatch)
    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert inspections == 4
    assert calls == {"mkstemp": 0, "fdopen": 0, "replace": 0}
    assert not (root / ".claude").exists()


@pytest.mark.parametrize("foreign_content", [False, True])
def test_late_journal_after_mkdir_is_blocked_and_owned_directory_is_safe(
    tmp_path, monkeypatch, foreign_content,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    original = Path.mkdir

    def appear_after_mkdir(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == root / ".claude":
            if foreign_content:
                (path / "user.txt").write_bytes(b"user")
            _create_transaction(root)
        return result

    monkeypatch.setattr(Path, "mkdir", appear_after_mkdir)
    calls = _forbid_claude_publication(monkeypatch)
    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, None, CLAUDE_BRIDGE_BYTES)
    assert calls == {"mkstemp": 0, "fdopen": 0, "replace": 0}
    if foreign_content:
        assert (root / ".claude/user.txt").read_bytes() == b"user"
    else:
        assert not (root / ".claude").exists()
    assert not list(root.rglob(".aef-claude-*.tmp"))


def test_late_journal_before_staging_in_existing_directory_blocks_update(
    tmp_path, monkeypatch,
):
    import aef.claude_filesystem as filesystem

    root = workspace(tmp_path)
    target = root / ".claude/CLAUDE.md"
    target.parent.mkdir()
    target.write_bytes(b"before")
    original = filesystem._evaluation_transaction_entry_present
    inspections = 0

    def appear_after_bridge_guard(path):
        nonlocal inspections
        inspections += 1
        present = original(path)
        if inspections == 3:
            _create_transaction(root)
        return present

    monkeypatch.setattr(
        filesystem, "_evaluation_transaction_entry_present",
        appear_after_bridge_guard,
    )
    calls = _forbid_claude_publication(monkeypatch)
    with pytest.raises(EvaluationRecoveryRequiredError):
        apply_claude_bridge(root, b"before", b"after")
    assert inspections == 4
    assert calls == {"mkstemp": 0, "fdopen": 0, "replace": 0}
    assert target.read_bytes() == b"before"
    assert not list(root.rglob(".aef-claude-*.tmp"))
