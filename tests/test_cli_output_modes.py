import io
import json

import pytest

import aef.cli as cli


class TerminalBuffer(io.StringIO):
    def __init__(self, *, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def envelope(tmp_path, *, command="AUDIT", status="PASS", result=None, diff=None):
    return cli._envelope(
        command=command,
        workspace=tmp_path,
        status=status,
        ok=status in {"CHANGE", "NO_CHANGE", "PASS"},
        dry_run=False,
        result=result or {"schema_version": "1.0.0", "findings": []},
        meta={},
        diff=diff,
    )


def invoke(monkeypatch, tmp_path, argv, *, tty, response=None, code=0):
    stdout = TerminalBuffer(tty=tty)
    stderr = TerminalBuffer(tty=False)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(
        cli, "_run_command", lambda args: (
            response if response is not None else envelope(tmp_path), code
        )
    )
    exit_code = cli.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize(
    ("argv", "tty", "human"),
    [
        (["audit"], True, True),
        (["audit"], False, False),
        (["--json", "audit"], True, False),
        (["--human", "audit"], False, True),
        (["--compact", "audit"], True, False),
    ],
)
def test_output_mode_selection(monkeypatch, tmp_path, argv, tty, human):
    code, stdout, _ = invoke(monkeypatch, tmp_path, argv, tty=tty)

    assert code == 0
    if human:
        assert stdout.startswith("[OK] AEF audit passed\n")
        assert "\"api_version\"" not in stdout
    else:
        assert json.loads(stdout)["api_version"] == "aef.cli/v1"
        assert "[OK]" not in stdout


@pytest.mark.parametrize("argv", [
    ["--json", "--human", "audit"],
    ["--compact", "--human", "audit"],
])
def test_output_mode_conflicts_are_argument_errors(monkeypatch, argv):
    monkeypatch.setattr(cli.sys, "stdout", TerminalBuffer(tty=False))
    monkeypatch.setattr(cli.sys, "stderr", TerminalBuffer(tty=False))

    with pytest.raises(SystemExit) as stopped:
        cli.main(argv)

    assert stopped.value.code == 2


@pytest.mark.parametrize(
    ("response", "expected", "code"),
    [
        ({"command": "INIT", "status": "CHANGE"}, "[OK] AEF initialized", 0),
        ({"command": "INIT", "status": "NO_CHANGE"}, "[OK] AEF is already initialized", 0),
        ({"command": "INIT", "status": "BLOCKED"}, "[BLOCKED]", 4),
        ({"command": "AUDIT", "status": "PASS"}, "[OK] AEF audit passed", 0),
        ({"command": "AUDIT", "status": "FAIL"}, "[FAILED] AEF audit found problems", 1),
    ],
)
def test_human_status_headings(monkeypatch, tmp_path, response, expected, code):
    document = envelope(
        tmp_path,
        command=response["command"],
        status=response["status"],
        result={"schema_version": "1.0.0", "findings": ["problem"]},
        diff={"created": [".agent/manifest.json"], "modified": [], "removed": []},
    )
    actual_code, stdout, _ = invoke(
        monkeypatch, tmp_path, ["--human", response["command"].lower()],
        tty=False, response=document, code=code,
    )

    assert actual_code == code
    assert expected in stdout
    assert "\"api_version\"" not in stdout


@pytest.mark.parametrize(("value", "expected"), [
    ("safe\n[OK] injected", r"safe\n[OK] injected"),
    ("tab\tcarriage\r", r"tab\tcarriage\r"),
    ("back\bform\f", r"back\bform\f"),
    ("ansi\x1b[31mred", r"ansi\x1b[31mred"),
    ("nul\x00 del\x7f c1\x85", r"nul\x00 del\x7f c1\x85"),
    ("bidi\u202e line\u2028 paragraph\u2029", r"bidi\u202e line\u2028 paragraph\u2029"),
    ("français 日本 😀", "français 日本 😀"),
    ("日本\n\x1b[2Jé", r"日本\n\x1b[2Jé"),
])
def test_human_value_escape_is_visible_single_line_and_preserves_text(value, expected):
    escaped = cli._escape_human_value(value)

    assert escaped == expected
    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "\t" not in escaped
    assert "\x1b" not in escaped


def test_dynamic_values_cannot_inject_status_or_ansi(monkeypatch, tmp_path):
    (tmp_path / ".agent").mkdir()
    document = envelope(
        tmp_path,
        command="AUDIT",
        status="FAIL",
        result={
            "findings": [
                "safe\n[OK] injected",
                {"id": "ansi\x1b[31mFAILED"},
            ]
        },
    )

    code, stdout, _ = invoke(
        monkeypatch, tmp_path, ["--human", "--workspace", str(tmp_path), "audit"], tty=False,
        response=document, code=1,
    )

    assert code == 1
    workspace_line = f"Workspace : {cli._display_workspace(document)}"
    assert stdout.splitlines() == [
        "[FAILED] AEF audit found problems",
        "",
        r"- safe\n[OK] injected",
        r"- ansi\x1b[31mFAILED",
        workspace_line,
    ]
    assert stdout.count("[OK]") == 1


def valid_audit_envelope(tmp_path):
    return envelope(
        tmp_path, command="AUDIT", status="FAIL",
        result={"schema_version": "1.0.0", "findings": ["problem"]},
    )


@pytest.mark.parametrize("missing", [
    "command", "status", "workspace", "result", "meta", "diff", "error",
])
def test_missing_common_envelope_field_uses_incomplete_fallback(missing, tmp_path):
    document = valid_audit_envelope(tmp_path)
    del document[missing]

    assert cli._render_human(document) == cli._INCOMPLETE_HUMAN_RESULT


@pytest.mark.parametrize(("field", "value"), [
    ("command", None),
    ("status", []),
    ("workspace", None),
    ("result", []),
    ("meta", "invalid"),
    ("diff", "invalid"),
    ("error", {"raw": "private"}),
])
def test_malformed_common_envelope_field_uses_incomplete_fallback(field, value, tmp_path):
    document = valid_audit_envelope(tmp_path)
    document[field] = value

    output = cli._render_human(document)

    assert output == cli._INCOMPLETE_HUMAN_RESULT
    assert "private" not in output
    assert "{" not in output


def test_audit_fail_without_result_is_incomplete(tmp_path):
    document = valid_audit_envelope(tmp_path)
    document["result"] = {}

    assert cli._render_human(document) == cli._INCOMPLETE_HUMAN_RESULT


@pytest.mark.parametrize(("finding", "expected"), [
    ({"id": "missing-manifest", "private": "secret"}, "missing manifest"),
    ({"message": "Public message", "private": "secret"}, "Public message"),
    ({"private": "secret"}, "Unidentified audit finding"),
    (["private", "secret"], "Unidentified audit finding"),
])
def test_audit_findings_expose_only_recognized_public_values(finding, expected, tmp_path):
    document = valid_audit_envelope(tmp_path)
    document["result"]["findings"] = [finding]

    output = cli._render_human(document)

    assert f"- {expected}" in output
    assert "secret" not in output
    assert "{" not in output
    assert "[\"" not in output


def test_public_error_uses_only_sanitized_code_and_message(tmp_path):
    document = envelope(tmp_path, command="AUDIT", status="PASS")
    document.update({
        "status": "ERROR",
        "result": {},
        "error": {
            "code": "bad\n[OK] injected",
            "message": "safe\x1b[31merror",
            "details": {"private": "secret"},
        },
    })

    output = cli._render_human(document)

    assert output == (
        r"[ERROR] safe\x1b[31merror" "\n\n"
        r"Code      : bad\n[OK] injected" "\n"
    )
    assert "secret" not in output
    assert "{" not in output


def test_incomplete_internal_envelope_returns_70_with_one_stdout_write(
    monkeypatch, tmp_path
):
    class CountingTerminal(TerminalBuffer):
        calls = 0

        def write(self, value):
            self.calls += 1
            return super().write(value)

    stdout = CountingTerminal(tty=False)
    stderr = TerminalBuffer(tty=False)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)
    monkeypatch.setattr(cli, "_run_command", lambda args: ({"status": "PASS"}, 0))

    code = cli.main(["--human", "audit"])

    assert code == 70
    assert stdout.calls == 1
    assert stdout.getvalue() == cli._INCOMPLETE_HUMAN_RESULT
    assert "Traceback" not in stderr.getvalue()
