"""Guard: CLI subprocesses use ``python -m aef``, never the console script.

Exception: ``test_console_script_entry.py`` (registry in cli_invocation_audit).
"""

from __future__ import annotations

from pathlib import Path

from tests.support.cli_invocation_audit import (
    _CONSOLE_SCRIPT_INVOCATION_EXCEPTIONS,
    audit_tests_tree_for_console_script_cli,
    find_console_script_cli_invocations,
)

TESTS_ROOT = Path(__file__).resolve().parent


def test_tests_tree_does_not_invoke_aef_console_script():
    findings = audit_tests_tree_for_console_script_cli(TESTS_ROOT)
    assert findings == [], "\n".join(findings)


def test_console_script_exception_registry_is_sole_entry_point_file():
    assert _CONSOLE_SCRIPT_INVOCATION_EXCEPTIONS == frozenset(
        {"test_console_script_entry.py"}
    )


def test_console_script_invocation_guard_detects_new_sites():
    """Negative proof: a fresh console-script argv fails the auditor."""
    poisoned = '''
import subprocess
from conftest import installed_aef_script

def bad():
    script = installed_aef_script()
    subprocess.run([str(script), "--json", "doctor"])
'''
    findings = find_console_script_cli_invocations(poisoned, relative="poison.py")
    assert findings, "guard must report a new console-script subprocess site"
    assert any("console script" in item for item in findings)

    direct = '''
import subprocess, sys
from conftest import installed_aef_script

def bad():
    subprocess.run(
        [sys.executable, "-m", "aef"] if False else [str(installed_aef_script()), "doctor"]
    )
'''
    findings2 = find_console_script_cli_invocations(direct, relative="poison2.py")
    assert findings2, "guard must report IfExp branch that uses console script"
