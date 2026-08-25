from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked_ignored_paths():
    module = pytest.importorskip("scripts.check_tracked_gitignored")
    return module.tracked_ignored_paths


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _skip_unless_git_work_tree(root: Path = ROOT) -> None:
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("this tree is not a git work tree")


def _path_is_ignored(root: Path, path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"git check-ignore failed for {path!r}: "
            f"exit {result.returncode} {result.stderr}"
        )
    return result.returncode == 0


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
    assert _tracked_ignored_paths()(_repository(tmp_path)) == []


def test_forced_add_is_detected(tmp_path):
    repository = _repository(tmp_path)
    (repository / "secret.txt").write_text("nope\n", encoding="utf-8")
    _git(repository, "add", "-f", "secret.txt")
    _git(repository, "commit", "-q", "-m", "forced")
    assert "secret.txt" in _tracked_ignored_paths()(repository)


def test_current_repository_has_no_tracked_ignored_paths():
    _skip_unless_git_work_tree()
    assert _tracked_ignored_paths()(ROOT) == []


def test_ci_runs_the_tracked_ignored_check():
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.is_file():
        pytest.skip("ci workflow is not in this tree")
    assert "python scripts/check_tracked_gitignored.py" in workflow.read_text(encoding="utf-8")


# Three families of the distribution-artifact ignore rule.
#
# Ignored: dist/, dist-<suffix>/ at the root, and nested files under those
# trees. sub/dist/ is also ignored because the existing dist/ line is not
# root-anchored.
#
# Not ignored: distribution/ and distinct/. That is the control that turns
# red if the rule is widened to dist*/.
#
# Not ignored off-root: sub/dist-lot6/. /dist-*/ is root-anchored on
# purpose. Do not "fix" that anchor to make the two dist lines look
# symmetric: dropping the slash would reopen distribution/.
_DISTRIBUTION_ARTIFACT_IGNORE_CASES = (
    ("dist/x.whl", True, "ignored-standard-dist"),
    ("dist-lot6/x.whl", True, "ignored-root-dist-suffix"),
    ("dist-prep-2.0.0/x.whl", True, "ignored-root-dist-prep"),
    ("dist-lot9-probe/a/b.whl", True, "ignored-nested-under-root-dist-suffix"),
    ("sub/dist/x.whl", True, "ignored-unanchored-dist-under-prefix"),
    ("distribution/x.whl", False, "kept-distribution"),
    ("distinct/notes.md", False, "kept-distinct"),
    ("sub/dist-lot6/x.whl", False, "kept-dist-suffix-off-root"),
)


@pytest.mark.parametrize(
    "path, ignored",
    [(path, ignored) for path, ignored, _id in _DISTRIBUTION_ARTIFACT_IGNORE_CASES],
    ids=[item_id for _path, _ignored, item_id in _DISTRIBUTION_ARTIFACT_IGNORE_CASES],
)
def test_local_distribution_artifact_ignore_rule(path: str, ignored: bool) -> None:
    _skip_unless_git_work_tree()
    assert _path_is_ignored(ROOT, path) is ignored
