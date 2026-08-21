import json
from pathlib import Path
import subprocess
import sys

import pytest

from aef.claude_integration import (
    CLAUDE_BRIDGE_BYTES, LEGACY_CLAUDE_BRIDGE_BYTES,
)
from aef.guidance_integration import AGENTS_BYTES, CLAUDE_ROOT_BYTES
from conftest import installed_aef_script


def launcher():
    return installed_aef_script()


def run_cli(workspace, *arguments):
    arguments = list(arguments)
    global_options = []
    while arguments and arguments[0] in {"--json", "--human", "--compact"}:
        global_options.append(arguments.pop(0))
    return subprocess.run(
        [str(launcher()), *global_options, "--workspace", str(workspace), *arguments],
        capture_output=True, text=True, check=False,
    )


def initialized_workspace(tmp_path):
    completed = run_cli(tmp_path, "init", "--role", "generalist-agent")
    assert completed.returncode == 0, completed.stderr
    return tmp_path


def test_cli_install_status_replay_remove_and_empty_file_contract(tmp_path):
    root = initialized_workspace(tmp_path)

    installed = run_cli(root, "--json", "integrate", "claude")
    status = run_cli(root, "--json", "integrate", "claude", "--status")
    replay = run_cli(root, "--json", "integrate", "claude")
    removed = run_cli(root, "--json", "integrate", "claude", "--remove")
    removed_replay = run_cli(root, "--json", "integrate", "claude", "--remove")

    assert [item.returncode for item in (
        installed, status, replay, removed, removed_replay
    )] == [0, 0, 0, 0, 0]
    assert [json.loads(item.stdout)["status"] for item in (
        installed, status, replay, removed, removed_replay
    )] == ["CHANGE", "NO_CHANGE", "NO_CHANGE", "CHANGE", "NO_CHANGE"]
    assert (root / "CLAUDE.md").is_file()
    assert (root / "CLAUDE.md").read_bytes() == b""
    assert (root / "AGENTS.md").read_bytes() == AGENTS_BYTES
    assert not (root / ".claude").exists()


def test_cli_dry_run_matches_real_bytes_and_preserves_user_files(tmp_path):
    root = initialized_workspace(tmp_path)
    root_claude = root / "CLAUDE.md"
    root_claude.write_bytes(b"root instructions\r\n")
    bridge = root / ".claude/CLAUDE.md"
    bridge.parent.mkdir()
    bridge.write_bytes(b"# Existing user instructions")
    settings = bridge.parent / "settings.json"
    settings.write_bytes(b'{"hooks":{"SessionStart":[]}}')

    dry = run_cli(root, "--json", "integrate", "claude", "--dry-run")
    assert dry.returncode == 0
    assert json.loads(dry.stdout)["status"] == "CHANGE"
    assert bridge.read_bytes() == b"# Existing user instructions"
    assert root_claude.read_bytes() == b"root instructions\r\n"
    real = run_cli(root, "--json", "integrate", "claude")

    assert real.returncode == 0
    assert bridge.read_bytes() == b"# Existing user instructions"
    assert root_claude.read_bytes().startswith(b"root instructions\r\n\n\n")
    assert CLAUDE_ROOT_BYTES in root_claude.read_bytes()
    assert settings.read_bytes() == b'{"hooks":{"SessionStart":[]}}'


def test_status_warns_about_unmanaged_malformed_settings_without_failure(tmp_path):
    root = initialized_workspace(tmp_path)
    assert run_cli(root, "--json", "integrate", "claude").returncode == 0
    settings = root / ".claude/settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_bytes(b"not json")

    completed = run_cli(root, "--json", "integrate", "claude", "--status")
    envelope = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert envelope["result"]["bridge_healthy"] is True
    assert envelope["result"]["warnings"] == [
        "unmanaged_settings_json_invalid"
    ]
    assert settings.read_bytes() == b"not json"


def test_user_scope_is_explicit_stable_code_3(tmp_path):
    completed = run_cli(
        tmp_path, "--json", "integrate", "claude", "--scope", "user"
    )
    envelope = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert envelope["error"]["code"] == "unsupported_integration_scope"


def test_human_install_uses_guidance_only_language(tmp_path):
    root = initialized_workspace(tmp_path)
    completed = run_cli(root, "--human", "integrate", "claude")
    assert completed.returncode == 0
    assert "Guidance integration" in completed.stdout
    assert "Enforcement : guidance only" in completed.stdout
    assert "{" not in completed.stdout


@pytest.mark.parametrize("mode", ["--human", "--json", "--compact"])
@pytest.mark.parametrize("kind", ["script", "module"])
def test_installed_and_module_launchers_support_every_output_mode(
    tmp_path, mode, kind
):
    root = initialized_workspace(tmp_path)
    prefix = (
        [sys.executable, "-m", "aef"] if kind == "module" else [str(launcher())]
    )
    completed = subprocess.run(
        [*prefix, mode, "--workspace", str(root), "integrate", "claude"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    assert "Traceback" not in completed.stdout + completed.stderr
    if mode == "--human":
        assert "Guidance integration" in completed.stdout
        assert '"api_version"' not in completed.stdout
    else:
        assert json.loads(completed.stdout)["command"] == "INTEGRATE"
        if mode == "--compact":
            assert completed.stdout.count("\n") == 1


def test_legacy_bridge_is_not_rewritten_on_install_and_remove_falls_back(tmp_path):
    root = initialized_workspace(tmp_path)
    bridge = root / ".claude/CLAUDE.md"
    bridge.parent.mkdir()
    original = b"user\r\n\n\n" + LEGACY_CLAUDE_BRIDGE_BYTES + b"tail"
    bridge.write_bytes(original)

    dry = run_cli(root, "--json", "integrate", "claude", "--dry-run")
    assert dry.returncode == 0 and bridge.read_bytes() == original
    assert run_cli(root, "--json", "integrate", "claude").returncode == 0
    assert bridge.read_bytes() == original
    assert (root / "CLAUDE.md").read_bytes() == CLAUDE_ROOT_BYTES

    # Remove root doorbell first.
    assert run_cli(root, "--json", "integrate", "claude", "--remove").returncode == 0
    assert (root / "CLAUDE.md").read_bytes() == b""
    assert bridge.read_bytes() == original

    # Second remove clears legacy only.
    assert run_cli(root, "--json", "integrate", "claude", "--remove").returncode == 0
    assert bridge.read_bytes() == b"user\r\ntail"


def test_modified_legacy_blocks_legacy_remove_but_allows_root_install(tmp_path):
    root = initialized_workspace(tmp_path)
    bridge = root / ".claude/CLAUDE.md"
    bridge.parent.mkdir()
    modified = CLAUDE_BRIDGE_BYTES.replace(b"guidance", b"authority", 1)
    bridge.write_bytes(modified)

    completed = run_cli(root, "--json", "integrate", "claude")
    assert completed.returncode == 0
    assert bridge.read_bytes() == modified
    assert (root / "CLAUDE.md").read_bytes() == CLAUDE_ROOT_BYTES

    assert run_cli(root, "--json", "integrate", "claude", "--remove").returncode == 0
    completed = run_cli(root, "--json", "integrate", "claude", "--remove")
    assert completed.returncode == 4
    assert json.loads(completed.stdout)["meta"]["reason"] == (
        "modified_claude_managed_block"
    )
    assert bridge.read_bytes() == modified


def test_evaluation_transaction_blocks_mutations_but_not_status(tmp_path):
    root = initialized_workspace(tmp_path)
    assert run_cli(root, "--json", "integrate", "claude").returncode == 0
    doorbell = root / "CLAUDE.md"
    before = doorbell.read_bytes()
    transaction = root / ".agent/state/evaluation-transaction.json"
    transaction.write_text("{}\n", encoding="utf-8")

    for option in ((), ("--remove",)):
        completed = run_cli(root, "--json", "integrate", "claude", *option)
        assert completed.returncode == 4
        assert json.loads(completed.stdout)["meta"]["reason"] == (
            "evaluation_recovery_required"
        )
        assert doorbell.read_bytes() == before
    status = run_cli(root, "--json", "integrate", "claude", "--status")
    assert status.returncode == 0
    envelope = json.loads(status.stdout)
    assert envelope["result"]["bridge_healthy"] is True
    assert envelope["result"]["audit"] == "fail"


def test_status_distinguishes_healthy_bridge_from_failed_aef_audit(tmp_path):
    root = initialized_workspace(tmp_path)
    assert run_cli(root, "--json", "integrate", "claude").returncode == 0
    (root / ".agent/knowledge/knowledge.json").unlink()
    completed = run_cli(root, "--json", "integrate", "claude", "--status")
    envelope = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert envelope["result"]["bridge_healthy"] is True
    assert envelope["result"]["audit"] == "fail"


def test_missing_doctrine_is_blocked_code_4_without_writing_doors(tmp_path):
    root = initialized_workspace(tmp_path)
    (root / ".agent/core/learning.md").unlink()
    completed = run_cli(root, "--json", "integrate", "claude")
    assert completed.returncode == 4
    assert json.loads(completed.stdout)["meta"]["reason"] == "missing_aef_doctrine"
    assert not (root / ".claude").exists()
    assert not (root / "CLAUDE.md").exists()
    assert not (root / "AGENTS.md").exists()


def test_unicode_workspace_is_safe_under_ascii_console_encoding(tmp_path):
    root = initialized_workspace(tmp_path / "Claude intégration 日本")
    environment = dict(__import__("os").environ)
    environment["PYTHONIOENCODING"] = "ascii:strict"
    completed = subprocess.run(
        [str(launcher()), "--human", "--workspace", str(root), "integrate", "claude"],
        capture_output=True, check=False, env=environment,
    )
    text = completed.stdout.decode("ascii")
    assert completed.returncode == 0
    assert "\\u65e5\\u672c" in text
    assert "Traceback" not in text


def test_status_preserves_both_unmanaged_settings_and_existing_hooks(tmp_path):
    root = initialized_workspace(tmp_path)
    assert run_cli(root, "--json", "integrate", "claude").returncode == 0
    shared = root / ".claude/settings.json"
    local = root / ".claude/settings.local.json"
    shared.parent.mkdir(exist_ok=True)
    shared.write_bytes(b'{"hooks":{"SessionStart":[{"matcher":"startup"}]}}')
    local.write_bytes(b"invalid local settings")
    before = {shared: shared.read_bytes(), local: local.read_bytes()}
    completed = run_cli(root, "--json", "integrate", "claude", "--status")
    envelope = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert envelope["result"]["warnings"] == [
        "unmanaged_settings_local_json_invalid"
    ]
    assert {path: path.read_bytes() for path in before} == before


def test_fsync_failure_returns_public_filesystem_error_without_success(
    tmp_path, monkeypatch, capsys,
):
    import aef.guidance_filesystem as filesystem
    from aef.cli import main

    root = initialized_workspace(tmp_path)
    monkeypatch.setattr(
        filesystem.os, "fsync",
        lambda descriptor: (_ for _ in ()).throw(OSError("secret fsync detail")),
    )

    code = main([
        "--json", "--workspace", str(root), "integrate", "agents",
    ])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)

    assert code == 6
    assert envelope["status"] == "ERROR"
    assert envelope["error"]["code"] in {
        "claude_integration_filesystem_error",
        "filesystem_error",
        "guidance_filesystem_error",
    }
    assert "secret fsync detail" not in captured.out + captured.err
    assert not list(root.rglob(".aef-guidance-*.tmp"))
