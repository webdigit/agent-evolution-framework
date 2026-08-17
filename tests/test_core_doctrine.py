from pathlib import Path

from aef.filesystem import apply_workspace, load_workspace
from aef.init_profiles import get_init_profile
from aef.operations import init_project


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROOT = ROOT / "tests" / "expected_core"
CORE_NAMES = ("constitution", "autonomy", "learning", "levels", "scoring")


def expected_core_files():
    return {
        f".agent/core/{name}.md": (EXPECTED_ROOT / f"{name}.md").read_text(encoding="utf-8")
        for name in CORE_NAMES
    }


def test_official_core_doctrine_is_non_placeholder_and_matches_independent_oracle():
    actual = get_init_profile("aef-v1")["core_files"]
    expected = expected_core_files()

    assert actual == expected
    for path, content in actual.items():
        assert len(content.splitlines()) >= 10, path
        assert " MUST " in content, path
        assert content != f"# AEF {Path(path).stem.title()}\n"


def test_official_core_doctrine_contains_required_v1_invariants():
    core = get_init_profile("aef-v1")["core_files"]
    constitution = core[".agent/core/constitution.md"]
    autonomy = core[".agent/core/autonomy.md"]
    learning = core[".agent/core/learning.md"]
    levels = core[".agent/core/levels.md"]
    scoring = core[".agent/core/scoring.md"]

    assert "local to the explicitly selected project workspace" in constitution
    assert "MUST NOT implicitly inherit" in constitution
    assert "~/.claude/" in constitution
    assert "ALLOW_WITH_LOG" in constitution and "otherwise it MUST block or escalate" in constitution
    assert "Any integration" in autonomy and "canonical authorization policy" in autonomy
    assert "signal → observation → hypothesis → rule → principle" in learning
    assert "MUST NOT apply a promotion directly" in levels
    assert "Only an EVALUATE operation with explicit approval MAY apply a promotion" in levels
    assert "cases field MUST count all evaluated cases" in scoring
    assert "normative doctrine" in constitution
    assert "MUST NOT be treated as executable policy" in constitution


def test_aef_v1_init_writes_all_five_doctrines_byte_for_byte(tmp_path):
    current = load_workspace(tmp_path)
    status, desired, _ = init_project(
        current,
        instance_id="doctrine-agent",
        answers={"decision.role.primary.v1": "operator"},
        created_at="2026-08-14T10:00:00Z",
        profile="aef-v1",
    )

    diff = apply_workspace(tmp_path, current, desired)

    assert status == "CHANGE"
    expected = expected_core_files()
    assert set(expected).issubset(diff["created"])
    assert {
        path.relative_to(tmp_path).as_posix()
        for path in (tmp_path / ".agent/core").iterdir()
        if path.is_file()
    } == set(expected)
    for relative_path, content in expected.items():
        assert (tmp_path / relative_path).read_bytes() == content.encode("utf-8")
