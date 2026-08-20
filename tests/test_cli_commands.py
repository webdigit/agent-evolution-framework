import io
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from conftest import installed_aef_script

from aef import cli
from aef.filesystem import apply_workspace, load_workspace, render_workspace_plan
from aef.operations import init_project


ROLE = "decision.role.primary.v1"


class TtyInput(io.StringIO):
    def isatty(self):
        return True


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured


def init_args(workspace, *arguments):
    return ("--workspace", str(workspace), "init", *arguments)


def test_init_success_replay_and_stable_envelope(tmp_path, capsys):
    args = init_args(
        tmp_path, "--instance-id", "agent-1", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )

    first_code, first, _ = invoke(capsys, *args)
    second_code, second, _ = invoke(capsys, *init_args(tmp_path))

    assert first_code == second_code == 0
    assert first["status"] == "CHANGE"
    assert second["status"] == "NO_CHANGE"
    assert second["diff"] == {"created": [], "modified": [], "removed": []}
    assert second["result"] == {
        "framework_version": "1.0.0",
        "instance_id": "agent-1",
        "role": "generalist-agent",
        "schema_version": "1.0.0",
        "unresolved_decisions": [],
    }


def test_init_prompts_on_tty_and_uses_default_role(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", TtyInput("\n"))

    code, envelope, captured = invoke(
        capsys, *init_args(tmp_path, "--instance-id", "agent-1",
                          "--created-at", "2026-08-14T10:00:00Z")
    )

    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert "Primary role" in captured.err
    stored = load_workspace(tmp_path)
    assert stored["decisions"]["decisions"][0]["value"] == "generalist-agent"


def test_init_without_role_on_non_tty_is_blocked_without_writes(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    code, envelope, _ = invoke(capsys, *init_args(tmp_path))

    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["result"]["unresolved_decisions"] == [ROLE]
    assert envelope["diff"] == {"created": [], "modified": [], "removed": []}
    assert not (tmp_path / ".agent").exists()


def test_init_generates_reproducible_identity_and_timestamp(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: "generated-id")
    monkeypatch.setattr(cli, "_utc_now", lambda: "2026-08-14T12:34:56Z")

    _, envelope, _ = invoke(capsys, *init_args(tmp_path, "--role", "operator"))

    manifest = load_workspace(tmp_path)["files"][".agent/manifest.json"]
    assert envelope["result"]["instance_id"] == "generated-id"
    assert manifest["created_at"] == "2026-08-14T12:34:56Z"


@pytest.mark.parametrize(("arguments", "reason"), [
    (("--instance-id", "other"), "instance_id_mismatch"),
    (("--role", "other-role"), "decision_conflict"),
])
def test_init_explicit_conflicts_are_blocked_without_writes(
    tmp_path, capsys, arguments, reason
):
    base = init_args(
        tmp_path, "--instance-id", "agent-1", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    invoke(capsys, *base)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    code, envelope, _ = invoke(capsys, *init_args(tmp_path, *arguments))

    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert code == 4
    assert envelope["meta"]["reason"] == reason
    assert envelope["diff"] == {"created": [], "modified": [], "removed": []}
    assert after == before


def test_init_version_conflict_is_blocked(tmp_path, capsys):
    source = {
        "files": {
            ".agent/manifest.json": {
                "framework": "aef", "framework_version": "0.9.0",
                "schema_version": "1.0.0", "instance_id": "agent-1",
                "created_at": "2026-08-14T10:00:00Z",
            },
            ".agent/state/decisions.json": {"decisions": [{
                "id": ROLE, "status": "resolved", "value": "generalist-agent",
                "source": "human-confirmed",
            }]},
        }
    }
    apply_workspace(tmp_path, load_workspace(tmp_path), deepcopy(source))

    code, envelope, _ = invoke(capsys, *init_args(tmp_path))

    assert code == 4
    assert envelope["meta"]["reason"] == "framework_version_mismatch"


def test_init_dry_run_returns_full_diff_without_creating_agent_directory(tmp_path, capsys):
    code, envelope, _ = invoke(
        capsys, *init_args(tmp_path, "--instance-id", "agent-1", "--role", "operator",
                          "--created-at", "2026-08-14T10:00:00Z", "--dry-run")
    )

    assert code == 0
    assert envelope["status"] == "CHANGE"
    assert ".agent/state/decisions.json" in envelope["diff"]["created"]
    assert not (tmp_path / ".agent").exists()


@pytest.mark.parametrize(("arguments", "missing"), [
    (("--dry-run",), ["instance_id", "created_at"]),
    (("--dry-run", "--instance-id", "agent-1"), ["created_at"]),
    (("--dry-run", "--created-at", "2026-08-14T10:00:00Z"), ["instance_id"]),
])
def test_new_workspace_dry_run_requires_all_stable_inputs_before_generation(
    tmp_path, capsys, monkeypatch, arguments, missing
):
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: pytest.fail("UUID must not be generated"))
    monkeypatch.setattr(cli, "_utc_now", lambda: pytest.fail("time must not be generated"))

    code, envelope, _ = invoke(capsys, *init_args(tmp_path, *arguments))

    assert code == 3
    assert envelope["error"] == {
        "code": "dry_run_requires_stable_inputs",
        "message": (
            "INIT dry-run requires --instance-id and --created-at. "
            "Reuse the same values when applying the initialization."
        ),
        "details": {
            "missing": missing,
            "required_options": ["--instance-id", "--created-at"],
            "reuse_for_apply": True,
        },
    }
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["--json", "--compact"])
def test_new_workspace_dry_run_reports_stable_inputs_in_machine_modes(
    tmp_path, capsys, mode
):
    code = cli.main([
        mode, "--workspace", str(tmp_path), "init", "--role", "operator", "--dry-run",
    ])
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)

    assert code == 3
    assert envelope["error"]["code"] == "dry_run_requires_stable_inputs"
    assert envelope["error"]["details"] == {
        "missing": ["instance_id", "created_at"],
        "required_options": ["--instance-id", "--created-at"],
        "reuse_for_apply": True,
    }
    assert list(tmp_path.iterdir()) == []


def test_new_workspace_dry_run_explains_stable_inputs_in_human_mode(tmp_path, capsys):
    code = cli.main([
        "--human", "--workspace", str(tmp_path), "init", "--role", "operator", "--dry-run",
    ])
    captured = capsys.readouterr()

    assert code == 3
    assert captured.out == (
        "[ERROR] INIT dry-run requires --instance-id and --created-at. "
        "Reuse the same values when applying the initialization.\n\n"
        "Code      : dry_run_requires_stable_inputs\n"
    )
    assert list(tmp_path.iterdir()) == []


def test_explicit_dry_run_plan_matches_equivalent_real_write_bytes(tmp_path, capsys):
    outside = tmp_path / "project-owned.txt"
    outside.write_bytes(b"project-owned\n")
    arguments = (
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    current = load_workspace(tmp_path)
    status, desired, _ = init_project(
        current,
        instance_id="agent-1",
        answers={ROLE: "operator"},
        created_at="2026-08-14T10:00:00Z",
        profile="aef-v1",
    )
    expected_diff, rendered = render_workspace_plan(current, desired)

    dry_code, dry, _ = invoke(capsys, *init_args(tmp_path, *arguments, "--dry-run"))
    assert dry_code == 0
    assert dry["diff"] == expected_diff
    assert not (tmp_path / ".agent").exists()
    assert outside.read_bytes() == b"project-owned\n"

    real_code, real, _ = invoke(capsys, *init_args(tmp_path, *arguments))
    assert real_code == 0
    assert real["diff"] == dry["diff"]
    planned_paths = set(dry["diff"]["created"])
    assert dry["diff"]["modified"] == []
    assert dry["diff"]["removed"] == []
    assert set(rendered) == planned_paths

    written_paths = {
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / ".agent").rglob("*")
        if path.is_file()
    }
    assert written_paths == planned_paths
    for relative_path, content in rendered.items():
        assert (tmp_path / relative_path).read_bytes() == content.encode("utf-8")

    assert (tmp_path / ".agent/manifest.json").read_bytes() == (
        b'{\n'
        b'  "created_at": "2026-08-14T10:00:00Z",\n'
        b'  "framework": "aef",\n'
        b'  "framework_version": "1.0.0",\n'
        b'  "instance_id": "agent-1",\n'
        b'  "schema_version": "1.0.0"\n'
        b'}\n'
    )
    assert (tmp_path / ".agent/state/decisions.json").read_bytes() == (
        b'{\n'
        b'  "decisions": [\n'
        b'    {\n'
        b'      "id": "decision.role.primary.v1",\n'
        b'      "source": "human-confirmed",\n'
        b'      "status": "resolved",\n'
        b'      "value": "operator"\n'
        b'    }\n'
        b'  ]\n'
        b'}\n'
    )
    expected_constitution = (
        Path(__file__).parent / "expected_core" / "constitution.md"
    ).read_text(encoding="utf-8").encode("utf-8")
    assert (tmp_path / ".agent/core/constitution.md").read_bytes() == expected_constitution
    assert outside.read_bytes() == b"project-owned\n"
    assert not any(path.name.endswith(".tmp") for path in tmp_path.rglob("*"))
    assert {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file() and ".agent" not in path.relative_to(tmp_path).parts
    } == {"project-owned.txt"}

    replay_code, replay, _ = invoke(capsys, *init_args(tmp_path, "--dry-run"))
    assert replay_code == 0
    assert replay["status"] == "NO_CHANGE"
    assert replay["diff"] == {"created": [], "modified": [], "removed": []}


def test_audit_pass_and_fail_are_read_only(tmp_path, capsys):
    missing_code, missing, _ = invoke(capsys, "--workspace", str(tmp_path), "audit")
    assert missing_code == 1
    assert missing["status"] == "FAIL"
    assert missing["diff"] is None
    assert not (tmp_path / ".agent").exists()

    invoke(capsys, *init_args(
        tmp_path, "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-14T10:00:00Z",
    ))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    pass_code, passed, _ = invoke(capsys, "--workspace", str(tmp_path), "audit")
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert pass_code == 0
    assert passed["status"] == "PASS"
    assert passed["result"]["schema_version"] == "1.0.0"
    assert after == before


def test_workspace_with_spaces_and_compact_output(tmp_path, capsys):
    workspace = tmp_path / "workspace with spaces"
    code, envelope, captured = invoke(
        capsys, "--workspace", str(workspace), "--compact", "init",
        "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-14T10:00:00Z",
    )

    assert code == 0
    assert captured.out.count("\n") == 1
    assert envelope["workspace"] == workspace.resolve().as_posix()


def test_malformed_json_is_invalid_configuration(tmp_path, capsys):
    manifest = tmp_path / ".agent" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{broken", encoding="utf-8")

    code, envelope, captured = invoke(capsys, "--workspace", str(tmp_path), "audit")

    assert code == 3
    assert envelope["error"] == {
        "code": "invalid_json",
        "message": "A JSON document is invalid.",
        "details": {},
    }
    assert str(manifest) not in captured.out
    assert captured.err.startswith("aef:")


@pytest.mark.parametrize(("exception", "exit_code", "error_code"), [
    (PermissionError("denied"), 6, "filesystem_error"),
    (RuntimeError("private-runtime-detail"), 70, "internal_error"),
])
def test_recognized_command_failures_stay_in_json_envelope(
    tmp_path, capsys, monkeypatch, exception, exit_code, error_code
):
    monkeypatch.setattr(cli, "load_workspace", lambda _workspace: (_ for _ in ()).throw(exception))

    code, envelope, captured = invoke(capsys, "--workspace", str(tmp_path), "audit")

    assert code == exit_code
    assert envelope["error"]["code"] == error_code
    assert str(exception) not in captured.out
    assert str(exception) not in captured.err
    assert "Traceback" not in captured.err


def test_init_preserves_files_outside_agent(tmp_path, capsys):
    outside = tmp_path / "project.txt"
    outside.write_text("owned by user\n", encoding="utf-8")

    invoke(capsys, *init_args(
        tmp_path, "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-14T10:00:00Z",
    ))

    assert outside.read_text(encoding="utf-8") == "owned by user\n"


@pytest.mark.parametrize("launcher", ["module", "script"])
def test_module_and_installed_script_emit_one_json_document_and_real_exit_code(
    tmp_path, launcher
):
    python = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(python), "-m", "aef"] if launcher == "module" else [str(script)]
    completed = subprocess.run(
        [*prefix, "--workspace", str(tmp_path), "audit"],
        capture_output=True,
        text=True,
        check=False,
    )

    document, end = json.JSONDecoder().raw_decode(completed.stdout)
    assert completed.returncode == 1
    assert document["command"] == "AUDIT"
    assert document["status"] == "FAIL"
    assert completed.stdout[end:].strip() == ""
    assert completed.stderr == ""


@pytest.mark.parametrize("created_at", ["2026-08-14", "2026-08-14T10:00:00"])
def test_init_rejects_non_rfc3339_timestamp(tmp_path, capsys, created_at):
    code, envelope, _ = invoke(
        capsys, *init_args(tmp_path, "--role", "operator", "--created-at", created_at)
    )

    assert code == 3
    assert envelope["error"]["code"] == "invalid_timestamp"
    assert not (tmp_path / ".agent").exists()


@pytest.mark.parametrize("document", [
    [],
    "invalid",
    None,
    {},
    {"decisions": "invalid"},
    {"decisions": {}},
    {"decisions": None},
    {"decisions": ["invalid"]},
    {"decisions": [{}]},
    {"decisions": [{"id": 42}]},
    {"decisions": [{"id": ""}]},
    {"decisions": [{"id": "   "}]},
    {"decisions": [
        {"id": "duplicate", "status": "resolved", "value": 1, "source": "human"},
        {"id": "duplicate", "status": "resolved", "value": 2, "source": "human"},
    ]},
    {"decisions": [{"id": "other", "value": "value", "source": "human"}]},
    {"decisions": [{"id": "other", "status": "unknown", "value": "value", "source": "human"}]},
    {"decisions": [{"id": "other", "status": "resolved", "value": "value"}]},
    {"decisions": [{"id": "other", "status": "resolved", "value": "value", "source": ""}]},
    {"decisions": [{"id": "other", "status": "resolved", "value": "value", "source": 42}]},
    {"decisions": [{"id": ROLE, "status": "resolved"}]},
    {"decisions": [{"id": ROLE, "status": "resolved", "value": "", "source": "human"}]},
    {"decisions": [{"id": ROLE, "status": "resolved", "value": [], "source": "human"}]},
    {"decisions": [{"id": ROLE, "value": "operator"}]},
])
def test_subprocess_rejects_malformed_decisions_document_without_writes(tmp_path, document):
    agent_state = tmp_path / ".agent" / "state"
    agent_state.mkdir(parents=True)
    decisions_path = agent_state / "decisions.json"
    decisions_path.write_text(json.dumps(document), encoding="utf-8")
    before = decisions_path.read_bytes()
    python = Path(sys.executable)

    completed = subprocess.run(
        [str(python), "-m", "aef", "--workspace", str(tmp_path), "init",
         "--instance-id", "agent-1", "--created-at", "2026-08-14T10:00:00Z",
         "--role", "operator"],
        capture_output=True,
        text=True,
        check=False,
    )
    envelope = json.loads(completed.stdout)

    assert completed.returncode == 3
    assert envelope["error"] == {
        "code": "invalid_decisions_document",
        "message": "The decisions document is invalid.",
        "details": {},
    }
    assert decisions_path.read_bytes() == before
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()) == [
        ".agent/state/decisions.json"
    ]


@pytest.mark.parametrize("verbose", [False, True])
def test_subprocess_json_error_has_stable_stdout_and_filtered_stderr(
    tmp_path, verbose
):
    manifest = tmp_path / ".agent" / "manifest.json"
    manifest.parent.mkdir()
    secret = "do-not-disclose-this-token"
    manifest.write_text("{" + secret, encoding="utf-8")
    python = Path(sys.executable)
    command = [str(python), "-m", "aef", "--workspace", str(tmp_path)]
    if verbose:
        command.append("--verbose")
    command.append("audit")

    completed = subprocess.run(command, input="", capture_output=True, text=True, check=False)
    envelope, end = json.JSONDecoder().raw_decode(completed.stdout)

    assert completed.returncode == 3
    assert envelope["error"] == {
        "code": "invalid_json",
        "message": "A JSON document is invalid.",
        "details": {},
    }
    assert completed.stdout[end:].strip() == ""
    assert secret not in completed.stdout
    assert secret not in completed.stderr
    assert "Traceback" not in completed.stderr
    assert ("JSONDecodeError" in completed.stderr) is verbose


@pytest.mark.parametrize("verbose", [False, True])
def test_subprocess_blocked_init_keeps_stdout_and_stderr_separate(tmp_path, verbose):
    python = Path(sys.executable)
    command = [str(python), "-m", "aef", "--workspace", str(tmp_path)]
    if verbose:
        command.append("--verbose")
    command.extend([
        "init", "--instance-id", "agent-1", "--created-at", "2026-08-14T10:00:00Z",
    ])

    completed = subprocess.run(command, input="", capture_output=True, text=True, check=False)
    envelope, end = json.JSONDecoder().raw_decode(completed.stdout)

    assert completed.returncode == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["error"] is None
    assert completed.stdout[end:].strip() == ""
    assert (completed.stderr == "") is (not verbose)
    if verbose:
        assert completed.stderr == "aef: INIT BLOCKED\n"


def test_subprocess_filesystem_error_uses_public_message(tmp_path):
    workspace = tmp_path / "workspace"
    manifest = workspace / ".agent" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hooks.joinpath("sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "_read_text = Path.read_text\n"
        "def denied(self, *args, **kwargs):\n"
        "    if self.name == 'manifest.json':\n"
        "        raise PermissionError('private operating-system detail')\n"
        "    return _read_text(self, *args, **kwargs)\n"
        "Path.read_text = denied\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(hooks) + os.pathsep + environment.get("PYTHONPATH", "")
    python = Path(sys.executable)

    completed = subprocess.run(
        [str(python), "-m", "aef", "--workspace", str(workspace), "audit"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    envelope = json.loads(completed.stdout)

    assert completed.returncode == 6
    assert envelope["error"] == {
        "code": "filesystem_error",
        "message": "The workspace could not be accessed.",
        "details": {},
    }
    assert "private operating-system detail" not in completed.stdout
    assert "private operating-system detail" not in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("launcher", ["module", "script"])
def test_subprocess_cli_protocol_is_cp1252_safe_for_unicode_workspace(tmp_path, launcher):
    workspace = tmp_path / "workspace espaces é 日本"
    python = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(python), "-m", "aef"] if launcher == "module" else [str(script)]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "cp1252:strict"
    common = ["--workspace", str(workspace), "--compact"]
    scenarios = [
        ([*common, "init", "--instance-id", "agent-1", "--role", "operator",
          "--created-at", "2026-08-14T10:00:00Z", "--dry-run"], 0, "CHANGE"),
        ([*common, "init", "--instance-id", "agent-1", "--role", "operator",
          "--created-at", "2026-08-14T10:00:00Z"], 0, "CHANGE"),
        ([*common, "init"], 0, "NO_CHANGE"),
        ([*common, "audit"], 0, "PASS"),
    ]

    for command, expected_code, expected_status in scenarios:
        completed = subprocess.run(
            [*prefix, *command], capture_output=True, check=False, env=environment
        )
        output = completed.stdout.decode("cp1252")
        document, end = json.JSONDecoder().raw_decode(output)

        assert completed.returncode == expected_code
        assert document["status"] == expected_status
        assert document["workspace"] == workspace.resolve().as_posix()
        assert output[end:].strip() == ""
        assert completed.stderr.decode("cp1252") == ""
        assert b"Traceback" not in completed.stdout + completed.stderr

    assert (workspace / ".agent" / "manifest.json").is_file()


def test_subprocess_indented_json_is_ascii_safe_for_unicode_workspace(tmp_path):
    workspace = tmp_path / "workspace indenté 日本"
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "ascii:strict"
    python = Path(sys.executable)

    completed = subprocess.run(
        [str(python), "-m", "aef", "--workspace", str(workspace), "audit"],
        capture_output=True,
        check=False,
        env=environment,
    )
    output = completed.stdout.decode("ascii")
    document, end = json.JSONDecoder().raw_decode(output)

    assert completed.returncode == 1
    assert output.splitlines()[0] == "{"
    assert output.splitlines()[1].startswith("  ")
    assert document["workspace"] == workspace.resolve().as_posix()
    assert output[end:].strip() == ""
    assert completed.stderr == b""
    assert b"Traceback" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("launcher", ["module", "script"])
def test_forced_human_subprocess_init_replay_audit_and_blocked(tmp_path, launcher):
    workspace = tmp_path / "human workspace"
    blocked_workspace = tmp_path / "blocked workspace"
    python = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(python), "-m", "aef"] if launcher == "module" else [str(script)]
    common = [*prefix, "--human", "--workspace", str(workspace)]

    initialized = subprocess.run(
        [*common, "init", "--instance-id", "agent-1", "--role", "generalist-agent",
         "--created-at", "2026-08-14T10:00:00Z"],
        input="", capture_output=True, text=True, check=False,
    )
    replay = subprocess.run(
        [*common, "init"], input="", capture_output=True, text=True, check=False,
    )
    audit = subprocess.run(
        [*common, "audit"], input="", capture_output=True, text=True, check=False,
    )
    blocked = subprocess.run(
        [*prefix, "--human", "--workspace", str(blocked_workspace), "init",
         "--instance-id", "agent-2", "--created-at", "2026-08-14T10:00:00Z"],
        input="", capture_output=True, text=True, check=False,
    )

    assert initialized.returncode == 0
    assert initialized.stdout.startswith("[OK] AEF initialized\n\n")
    assert "Role      : generalist-agent" in initialized.stdout
    assert replay.returncode == 0
    assert replay.stdout.startswith("[OK] AEF is already initialized\n\n")
    assert audit.returncode == 0
    assert audit.stdout.startswith("[OK] AEF audit passed\n\n")
    assert blocked.returncode == 4
    assert blocked.stdout == (
        "[BLOCKED] AEF initialization requires a primary role\n\n"
        "Run:\n"
        "aef init --role generalist-agent\n"
    )
    for completed in (initialized, replay, audit, blocked):
        assert '"api_version"' not in completed.stdout
        assert "Traceback" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("encoding", ["utf-8:strict", "ascii:strict", "cp1252:strict"])
def test_human_unicode_workspace_escapes_only_when_terminal_requires_it(tmp_path, encoding):
    workspace = tmp_path / "espaces accents-é-日本"
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = encoding
    codec = encoding.split(":", 1)[0]
    python = Path(sys.executable)

    completed = subprocess.run(
        [str(python), "-m", "aef", "--human", "--workspace", str(workspace),
         "init", "--dry-run", "--instance-id", "agent-1", "--role", "operator",
         "--created-at", "2026-08-14T10:00:00Z"],
        input=b"", capture_output=True, check=False, env=environment,
    )
    output = completed.stdout.decode(codec)

    assert completed.returncode == 0
    assert output.splitlines()[0] == "[OK] AEF initialized"
    if codec == "utf-8":
        assert "é-日本" in output
    else:
        assert "\\u65e5\\u672c" in output
    assert '"api_version"' not in output
    assert b"Traceback" not in completed.stdout + completed.stderr


def test_human_and_json_modes_keep_identical_exit_codes(tmp_path):
    python = Path(sys.executable)
    base = [str(python), "-m", "aef", "--workspace", str(tmp_path)]

    human = subprocess.run(
        [*base, "--human", "audit"], capture_output=True, check=False
    )
    machine = subprocess.run(
        [*base, "--json", "audit"], capture_output=True, check=False
    )

    assert human.returncode == machine.returncode == 1
    assert human.stdout.startswith(b"[FAILED]")
    assert json.loads(machine.stdout)["status"] == "FAIL"


@pytest.mark.parametrize("launcher", ["module", "script"])
def test_subprocess_incomplete_human_envelope_is_fail_safe(tmp_path, launcher):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hooks.joinpath("sitecustomize.py").write_text(
        "import aef.cli as cli\n"
        "cli._run_command = lambda args: ({'status': 'PASS'}, 0)\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(hooks) + os.pathsep + environment.get("PYTHONPATH", "")
    python = Path(sys.executable)
    script = installed_aef_script()
    prefix = [str(python), "-m", "aef"] if launcher == "module" else [str(script)]

    completed = subprocess.run(
        [*prefix, "--human", "audit"], capture_output=True, text=True,
        check=False, env=environment,
    )

    assert completed.returncode == 70
    assert completed.stdout.replace("\r\n", "\n") == (
        "[ERROR] AEF returned an incomplete result\n\n"
        "Some result details are unavailable.\n"
    )
    assert "Traceback" not in completed.stdout + completed.stderr
    assert "{" not in completed.stdout


@pytest.mark.skipif(os.name != "posix", reason="real PTY test requires POSIX pty")
def test_auto_mode_uses_human_output_on_real_pty(tmp_path):
    import pty

    master, slave = pty.openpty()
    python = Path(sys.executable)
    process = subprocess.Popen(
        [str(python), "-m", "aef", "--workspace", str(tmp_path), "audit"],
        stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.PIPE,
    )
    os.close(slave)
    chunks = []
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(master)
    stderr = process.communicate(timeout=10)[1]

    assert process.returncode == 1
    assert b"[FAILED] AEF audit found problems" in b"".join(chunks)
    assert b'"api_version"' not in b"".join(chunks)
    assert b"Traceback" not in stderr


@pytest.mark.parametrize("instance_id", ["", " ", "   "])
@pytest.mark.parametrize("dry_run", [False, True])
def test_blank_explicit_instance_id_is_rejected_before_uuid(
    tmp_path, capsys, monkeypatch, instance_id, dry_run
):
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: pytest.fail("UUID must not be generated"))
    arguments = ["--instance-id", instance_id, "--role", "operator",
                 "--created-at", "2026-08-14T10:00:00Z"]
    if dry_run:
        arguments.append("--dry-run")

    code, envelope, _ = invoke(capsys, *init_args(tmp_path, *arguments))

    assert code == 3
    assert envelope["error"]["code"] == "invalid_instance_id"
    assert not (tmp_path / ".agent").exists()


@pytest.mark.parametrize("created_at", ["", " ", "   "])
def test_blank_timestamp_is_invalid_without_calling_clock(tmp_path, capsys, monkeypatch, created_at):
    monkeypatch.setattr(cli, "_utc_now", lambda: pytest.fail("clock must not be called"))

    code, envelope, _ = invoke(
        capsys, *init_args(tmp_path, "--instance-id", "agent-1", "--role", "operator",
                          "--created-at", created_at)
    )

    assert code == 3
    assert envelope["error"]["code"] == "invalid_timestamp"
    assert not (tmp_path / ".agent").exists()


@pytest.mark.parametrize("created_at", ["", " "])
def test_blank_timestamp_counts_as_missing_for_new_dry_run(tmp_path, capsys, monkeypatch, created_at):
    monkeypatch.setattr(cli, "_utc_now", lambda: pytest.fail("clock must not be called"))

    code, envelope, _ = invoke(
        capsys, *init_args(tmp_path, "--instance-id", "agent-1", "--role", "operator",
                          "--created-at", created_at, "--dry-run")
    )

    assert code == 3
    assert envelope["error"]["code"] == "dry_run_requires_stable_inputs"
    assert envelope["error"]["details"] == {
        "missing": ["created_at"],
        "required_options": ["--instance-id", "--created-at"],
        "reuse_for_apply": True,
    }


@pytest.mark.parametrize(("option", "value", "error_code"), [
    ("--instance-id", " ", "invalid_instance_id"),
    ("--created-at", " ", "invalid_timestamp"),
])
@pytest.mark.parametrize("dry_run", [False, True])
def test_blank_explicit_values_are_rejected_on_existing_workspace(
    tmp_path, capsys, monkeypatch, option, value, error_code, dry_run
):
    invoke(capsys, *init_args(
        tmp_path, "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-14T10:00:00Z",
    ))
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: pytest.fail("UUID must not be generated"))
    monkeypatch.setattr(cli, "_utc_now", lambda: pytest.fail("clock must not be called"))
    arguments = [option, value]
    if dry_run:
        arguments.append("--dry-run")

    code, envelope, _ = invoke(capsys, *init_args(tmp_path, *arguments))

    assert code == 3
    assert envelope["error"]["code"] == error_code
