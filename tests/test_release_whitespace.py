from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_release_whitespace import check_release_tree


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=repository, check=True,
        capture_output=True, text=True,
    )


def _repository(tmp_path: Path, content: bytes) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release@example.invalid")
    (repository / "document.md").write_bytes(content)
    _git(repository, "add", "document.md")
    _git(repository, "commit", "-q", "-m", "fixture")
    return repository


def test_complete_release_tree_is_clean(tmp_path):
    result = check_release_tree(_repository(tmp_path, b"# Clean\n\nContent.\n"))

    assert result.returncode == 0
    assert result.stdout == ""


def test_actual_release_candidate_tree_is_clean():
    result = check_release_tree(Path(__file__).resolve().parents[1])

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "content",
    [
        b"# Trailing space \n",
        b"# Extra terminal line\n\n",
    ],
)
def test_complete_release_tree_detects_whitespace_errors(tmp_path, content):
    result = check_release_tree(_repository(tmp_path, content))

    assert result.returncode != 0
    assert "document.md" in result.stdout


def test_repository_workflow_uses_complete_tree_checker():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "python scripts/check_release_whitespace.py" in workflow
    assert "git show --check HEAD" not in workflow
