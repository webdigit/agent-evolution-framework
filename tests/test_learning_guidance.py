"""Learning guidance door — pure render, stale freshness, provenance marker."""

from __future__ import annotations

import json

from aef.guidance_integration import (
    AGENTS_BYTES,
    doors_for_integration,
    inspect_learning_door,
    plan_learning_integration,
)
from aef.learning_card import (
    LEARNING_CARD_HONESTY_MARKER,
    render_learning_card,
    wrap_learning_segment,
)
from tests.test_cli_guidance import init_workspace, invoke
from tests.test_guidance_integration import _project
from tests.test_ingest_confirmation import (
    PATTERN,
    _ingest_human_correction,
)


def _knowledge_with_rule(**rule_overrides):
    rule = {
        "id": f"rule:{PATTERN}",
        "type": "rule",
        "status": "active",
        "pattern_key": PATTERN,
        "derived_from": f"hypothesis:{PATTERN}",
        "evidence_ids": ["o1", "o2"],
        "confirmations": 3,
        "explicit_human_validation": False,
    }
    rule.update(rule_overrides)
    return {
        "signals": [],
        "observations": [],
        "hypotheses": [],
        "rules": [rule],
        "principles": [],
    }


def test_doors_for_integration_includes_learning():
    assert doors_for_integration("all") == [
        "agents", "claude", "gemini", "runtime", "learning",
    ]
    assert doors_for_integration("learning") == ["learning"]


def test_render_learning_card_is_pure_and_deterministic():
    first = render_learning_card(_knowledge_with_rule())
    second = render_learning_card(_knowledge_with_rule())
    assert first == second
    assert LEARNING_CARD_HONESTY_MARKER in first
    assert "périmé" in first
    assert "aef integrate learning" in first
    assert f"`rule:{PATTERN}`" in first
    assert "**Type**: `rule`" in first
    assert "**Confirmations**: 3" in first
    assert "**Explicit human validation**: false" in first
    assert "Source hierarchy" in first
    assert "Doctrine" in first


def test_render_learning_card_empty_workspace_states_no_rules():
    card = render_learning_card({
        "signals": [],
        "observations": [],
        "hypotheses": [],
        "rules": [],
        "principles": [],
    })
    assert LEARNING_CARD_HONESTY_MARKER in card
    assert "no active learned rules or principles" in card
    assert wrap_learning_segment(card)


def test_stale_is_not_modified_and_status_does_not_write():
    expected = wrap_learning_segment(render_learning_card(_knowledge_with_rule()))
    stale = expected.replace(b"Confirmations**: 3", b"Confirmations**: 9", 1)
    inspection = inspect_learning_door(stale, expected)
    assert inspection["state"] == "stale"
    assert inspection["freshness"] == "stale"
    assert inspection["state"] != "modified"

    status, _, meta = plan_learning_integration(
        _project(), stale, expected_bytes=expected, status_only=True,
    )
    assert status == "NO_CHANGE"
    assert meta["reason"] == "learning_card_stale"
    assert meta["freshness"] == "stale"
    assert meta["bridge_healthy"] is False


def test_integrate_learning_empty_workspace_status_and_card(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "learning", "--dry-run",
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    assert not (tmp_path / "docs" / "knowledge.md").exists()

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "learning",
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    path = tmp_path / "docs" / "knowledge.md"
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert LEARNING_CARD_HONESTY_MARKER in content
    assert "no active learned rules or principles" in content
    assert "AEF:LEARNING:BEGIN" in content

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "learning", "--status",
    )
    assert code == 0 and envelope["status"] == "NO_CHANGE"
    door = envelope["result"]["doors"]["learning"]
    assert door["bridge"]["state"] == "installed"
    assert door["reason"] is None


def test_three_intakes_then_integrate_learning_observable(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    for record_id, event_id in (
        ("session-one", "hc-one-a"),
        ("session-two", "hc-two"),
        ("session-three", "hc-three"),
    ):
        extra = None
        if record_id == "session-one":
            extra = [
                {"id": "novel-one", "novel": True, "pattern_key": PATTERN},
                {"id": "hc-one-b", "kind": "human_correction", "pattern_key": PATTERN},
            ]
        code, env, _ = _ingest_human_correction(
            tmp_path, capsys,
            record_id=record_id,
            event_id=event_id,
            extra=extra,
        )
        assert code == 0 and env["status"] == "CHANGE"

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    assert len(knowledge["rules"]) == 1
    assert knowledge["rules"][0]["id"] == f"rule:{PATTERN}"

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "all",
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    knowledge_md = tmp_path / "docs" / "knowledge.md"
    agents_md = tmp_path / "AGENTS.md"
    assert knowledge_md.is_file()
    card = knowledge_md.read_text(encoding="utf-8")
    assert f"`rule:{PATTERN}`" in card
    assert LEARNING_CARD_HONESTY_MARKER in card
    assert "**Type**: `rule`" in card
    assert "**Derived from**:" in card
    assert b"docs/knowledge.md" in agents_md.read_bytes()
    assert b"aef integrate learning" in agents_md.read_bytes()
    assert agents_md.read_bytes() == AGENTS_BYTES

    replay_code, replay_env, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "learning",
    )
    assert replay_code == 0 and replay_env["status"] == "NO_CHANGE"

    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "integrate", "learning", "--remove",
    )
    assert code == 0 and envelope["status"] == "CHANGE"
    assert b"AEF:LEARNING" not in knowledge_md.read_bytes()
