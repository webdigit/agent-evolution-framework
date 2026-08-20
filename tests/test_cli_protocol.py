import json

import pytest


cli = pytest.importorskip("aef.cli")


EXPECTED_FIELDS = {
    "api_version",
    "command",
    "ok",
    "status",
    "workspace",
    "dry_run",
    "result",
    "meta",
    "diff",
    "error",
}


def test_protocol_envelope_has_all_stable_fields(tmp_path):
    envelope = cli._envelope(
        command="INIT",
        workspace=tmp_path,
        status="CHANGE",
        ok=True,
        dry_run=False,
        result={"instance_id": "agent-1"},
        meta={},
        diff={"created": [], "modified": [], "removed": []},
    )

    assert set(envelope) == EXPECTED_FIELDS
    assert envelope["api_version"] == "aef.cli/v1"
    assert envelope["workspace"] == tmp_path.resolve().as_posix()
    assert envelope["error"] is None


def test_default_output_is_one_indented_sorted_json_document(capsys, tmp_path):
    envelope = cli._envelope(
        command="AUDIT", workspace=tmp_path, status="PASS", ok=True,
        dry_run=False, result={}, meta={}, diff=None,
    )

    cli._write_envelope(envelope, compact=False)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n{") == 0
    assert captured.out.startswith("{\n")
    assert json.loads(captured.out) == envelope
    assert captured.out.index('"api_version"') < captured.out.index('"command"')


def test_compact_output_changes_only_json_presentation(capsys, tmp_path):
    envelope = cli._envelope(
        command="AUDIT", workspace=tmp_path, status="PASS", ok=True,
        dry_run=False, result={}, meta={}, diff=None,
    )

    cli._write_envelope(envelope, compact=True)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == envelope


def test_json_output_is_ascii_but_round_trips_unicode(capsys, tmp_path):
    envelope = cli._envelope(
        command="AUDIT", workspace=tmp_path / "é 日本", status="PASS", ok=True,
        dry_run=False, result={"label": "français 日本"}, meta={}, diff=None,
    )

    cli._write_envelope(envelope, compact=False)

    output = capsys.readouterr().out
    output.encode("ascii")
    assert json.loads(output) == envelope


@pytest.mark.parametrize(("exception", "expected"), [
    (BrokenPipeError(), 6),
    (UnicodeEncodeError("ascii", "é", 0, 1, "unsupported"), 6),
    (OSError("stream unavailable"), 6),
    (RuntimeError("unexpected stream failure"), 70),
])
@pytest.mark.parametrize("mode", [[], ["--human"]])
def test_final_output_boundary_never_retries_or_leaks(
    monkeypatch, tmp_path, exception, expected, mode
):
    class UnusableStdout:
        encoding = "ascii"
        calls = 0

        def write(self, _payload):
            self.calls += 1
            raise exception

    stdout = UnusableStdout()
    stderr = __import__("io").StringIO()
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    code = cli.main(["--workspace", str(tmp_path), *mode, "audit"])

    assert code == expected
    assert stdout.calls == 1
    assert "Traceback" not in stderr.getvalue()
    stderr.getvalue().encode("ascii")


@pytest.mark.parametrize(("command", "status", "expected"), [
    ("INIT", "CHANGE", 0),
    ("INIT", "NO_CHANGE", 0),
    ("AUDIT", "PASS", 0),
    ("AUDIT", "FAIL", 1),
    ("INIT", "BLOCKED", 4),
    ("RECORD", "CHANGE", 0),
    ("RECORD", "NO_CHANGE", 0),
    ("RECORD", "BLOCKED", 4),
    ("UPGRADE", "CHANGE", 0),
    ("UPGRADE", "NO_CHANGE", 0),
    ("UPGRADE", "BLOCKED", 4),
    ("UPGRADE", "FAILED", 5),
])
def test_protocol_exit_code_mapping(command, status, expected):
    assert cli._exit_code(command, status) == expected


def test_exit_code_five_is_reserved_for_future_business_failures():
    # INIT and AUDIT do not currently produce FAILED. The mapping remains part
    # of the protocol for a future operation that genuinely returns it.
    assert cli._exit_code("FUTURE_OPERATION", "FAILED") == 5


def test_help_and_version_are_argparse_outputs(capsys):
    with pytest.raises(SystemExit) as help_exit:
        cli.main(["--help"])
    help_output = capsys.readouterr()
    assert help_exit.value.code == 0
    assert "usage:" in help_output.out
    assert help_output.err == ""

    with pytest.raises(SystemExit) as version_exit:
        cli.main(["--version"])
    version_output = capsys.readouterr()
    assert version_exit.value.code == 0
    assert version_output.out.startswith("aef ")
    assert version_output.err == ""


def test_unknown_command_returns_argparse_code_two_without_json(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["unknown"])

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert captured.out == ""
    assert "invalid choice" in captured.err
