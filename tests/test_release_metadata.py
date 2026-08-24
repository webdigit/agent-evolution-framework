from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from aef._version import __version__
from aef.cli import API_VERSION, _distribution_version
from aef.claude_integration import CLAUDE_INTEGRATION_VERSION
from aef.init_profiles import get_init_profile


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_version_has_one_python_source_of_truth():
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == "2.0.0"
    assert _distribution_version() == __version__
    assert configuration["project"]["dynamic"] == ["version"]
    assert "version" not in configuration["project"]
    assert configuration["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "aef._version.__version__"
    }


def test_distribution_release_does_not_relabel_independent_contracts():
    profile = get_init_profile("aef-v1")

    assert API_VERSION == "aef.cli/v1"
    assert profile["id"] == "aef-v1"
    assert profile["framework_version"] == "1.0.0"
    assert profile["schema_version"] == "1.0.0"
    assert CLAUDE_INTEGRATION_VERSION == "1.0.0"


def test_module_entry_point_reports_release_version():
    result = subprocess.run(
        [sys.executable, "-m", "aef", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "aef 2.0.0"
    assert result.stderr == ""


def test_release_metadata_declares_supported_public_contract():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "LicenseRef-PolyForm-Internal-Use-1.0.0"
    assert "Development Status :: 5 - Production/Stable" in project["classifiers"]
    assert not any(item.startswith("License :: OSI Approved") for item in project["classifiers"])
    assert project["urls"]["Source"].endswith("webdigit/agent-evolution-framework")
