"""Smoke test for the published ``aef`` console script entry point.

The main suite invokes ``python -m aef`` because WDAC on some development hosts
blocks ``aef.exe``; this file is the sole allowed console-script subprocess site
(see ``tests/support/cli_invocation_audit.py``). CI Linux runs it without skip;
hosts where application control blocks the script skip with an explicit reason.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aef._version import __version__
from conftest import installed_aef_script

ROOT = Path(__file__).resolve().parents[1]

_CONSOLE_SCRIPT_SKIP_REASON = (
    "console script not executable on this host (application control policy)"
)


def _venv_python(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _run_console_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the published console script; skip loudly when WDAC blocks spawn."""
    try:
        return subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 4551:
            pytest.skip(_CONSOLE_SCRIPT_SKIP_REASON)
        raise


@pytest.fixture
def disposable_console_install(tmp_path: Path) -> tuple[Path, Path]:
    """Install this tree editable in a throwaway venv; return (script, python)."""
    venv_root = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = _venv_python(venv_root)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "-e", f"{ROOT}[dev]"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr or install.stdout
    scripts_dir = python.parent
    script = installed_aef_script(
        executable=python,
        scripts_directory=scripts_dir,
        path_lookup=lambda _name: None,
    )
    assert script.is_file(), f"console script missing under {scripts_dir}"
    return script, python


def test_published_console_script_matches_module_version(
    disposable_console_install: tuple[Path, Path],
):
    script, python = disposable_console_install
    module = subprocess.run(
        [str(python), "-m", "aef", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    console = _run_console_script(script, "--version")

    assert module.returncode == 0
    assert module.stderr == ""
    assert module.stdout.strip() == f"aef {__version__}"
    assert console.returncode == module.returncode
    assert console.stdout == module.stdout
    assert console.stderr == module.stderr


def test_published_console_script_matches_module_doctor_json(
    tmp_path: Path,
    disposable_console_install: tuple[Path, Path],
):
    script, python = disposable_console_install
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    module_args = [
        str(python), "-m", "aef",
        "--json", "--workspace", str(workspace), "doctor",
    ]
    module = subprocess.run(
        module_args,
        capture_output=True,
        text=True,
        check=False,
    )
    console = _run_console_script(
        script, "--json", "--workspace", str(workspace), "doctor",
    )

    assert module.returncode == console.returncode
    assert module.stderr == console.stderr
    module_doc = json.loads(module.stdout)
    console_doc = json.loads(console.stdout)
    assert console_doc == module_doc
    assert module_doc["command"] == "DOCTOR"
