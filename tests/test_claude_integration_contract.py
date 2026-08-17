from pathlib import Path


EXPECTED = Path(__file__).parent / "expected_claude" / "CLAUDE.md"


def test_canonical_claude_bridge_contract_is_project_local_and_guidance_only():
    content = EXPECTED.read_text(encoding="utf-8")

    assert content.startswith(
        '<!-- AEF:CLAUDE-PROJECT:BEGIN version="1.0.0" -->\n'
    )
    assert content.endswith("<!-- AEF:CLAUDE-PROJECT:END -->\n")
    assert content.count("AEF:CLAUDE-PROJECT:BEGIN") == 1
    assert content.count("AEF:CLAUDE-PROJECT:END") == 1
    assert "only to this project" in content
    assert "guidance, not complete technical enforcement" in content
    assert "Never approve, reject, refresh, or recover" in content
    assert "evaluation recovery is required" in content
    assert "aef --json evaluate --list" in content
    assert "PreToolUse" not in content
    assert "SessionStart" not in content


def test_canonical_claude_bridge_imports_exactly_the_five_core_doctrines():
    imports = [
        line for line in EXPECTED.read_text(encoding="utf-8").splitlines()
        if line.startswith("@")
    ]

    assert imports == [
        "@../.agent/core/constitution.md",
        "@../.agent/core/autonomy.md",
        "@../.agent/core/learning.md",
        "@../.agent/core/levels.md",
        "@../.agent/core/scoring.md",
    ]


def test_canonical_bridge_contains_no_user_or_parent_import():
    content = EXPECTED.read_text(encoding="utf-8")

    assert "@~/" not in content
    assert "%USERPROFILE%" not in content
    assert "settings.json" not in content
