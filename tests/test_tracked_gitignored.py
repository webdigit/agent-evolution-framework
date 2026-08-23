from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_tracked_gitignored import tracked_ignored_paths


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Ignore Test")
    _git(repository, "config", "user.email", "ignore@example.invalid")
    (repository / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (repository / "visible.txt").write_text("ok\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "visible.txt")
    _git(repository, "commit", "-q", "-m", "fixture")
    return repository


def test_clean_tree_has_no_tracked_ignored_paths(tmp_path):
    assert tracked_ignored_paths(_repository(tmp_path)) == []


def test_forced_add_is_detected(tmp_path):
    repository = _repository(tmp_path)
    (repository / "secret.txt").write_text("nope\n", encoding="utf-8")
    _git(repository, "add", "-f", "secret.txt")
    _git(repository, "commit", "-q", "-m", "forced")
    assert "secret.txt" in tracked_ignored_paths(repository)


def test_current_repository_has_no_tracked_ignored_paths():
    assert tracked_ignored_paths(Path(__file__).resolve().parents[1]) == []


def test_ci_runs_the_tracked_ignored_check():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/check_tracked_gitignored.py" in workflow
