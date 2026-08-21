from __future__ import annotations

import pytest

from aef.cli import _build_parser


def test_top_level_help_explains_project_scope_and_machine_output(capsys):
    parser = _build_parser()

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "project-local Agent Evolution Framework state" in output
    assert "Automation should pass --json explicitly" in output
    assert "UPGRADE" not in output


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["init", "--help"], "official AEF V1 profile"),
        (["audit", "--help"], "without modifying it"),
        (["discover", "--help"], "without granting authority"),
        (["consolidate", "--help"], "knowledge-rule lifecycles"),
        (["evaluate", "--help"], "explicit human decisions"),
        (["integrate", "--help"], "confined to this project"),
        (["integrate", "claude", "--help"], "V1 supports project only"),
        (["record", "--help"], "declared-fact"),
        (["upgrade", "--help"], "without writing"),
        (["doctor", "--help"], "Does not modify"),
    ],
)
def test_command_help_describes_the_available_contract(arguments, expected, capsys):
    parser = _build_parser()

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(arguments)

    assert raised.value.code == 0
    assert expected in capsys.readouterr().out


def test_claude_help_does_not_advertise_user_scope(capsys):
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["integrate", "claude", "--help"])

    output = capsys.readouterr().out
    assert "{project,user}" not in output
    assert "--scope project" in output
