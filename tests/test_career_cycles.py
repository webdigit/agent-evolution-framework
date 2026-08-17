
from copy import deepcopy
from aef.career_cycle import career_cycle_step, recover_significant_errors
from aef.promotion_recommendations import InvalidPromotionRecommendationStateError
import pytest


def base_agent(level="L1", comp_level="L1", trust=0.95, xp=0, cases=0):
    return {
        "career": {
            "level": level, "xp": xp, "cases": cases, "trust": trust,
            "complex_cases": 0, "recent_significant_errors": 0, "probation": False
        },
        "competencies": {
            "general": {
                "level": comp_level, "xp": xp, "cases": cases, "trust": trust,
                "complex_cases": 0, "recent_significant_errors": 0, "probation": False
            }
        }
    }


def task(risk="R0", difficulty="D3", **extra):
    out = {"competency": "general", "risk": risk, "difficulty": difficulty}
    out.update(extra)
    return out


def test_l1_known_read_task_completes_but_stays_supervised():
    a = base_agent("L1", "L1", 0.95)
    r = career_cycle_step(a, task("R0"), reward=1)
    assert r["status"] == "COMPLETED"
    assert r["supervision_required"] is True
    assert r["agent"]["career"]["cases"] == 1
    assert r["agent"]["evaluations"]["schema_version"] == "1.0.0"


def test_l1_cannot_jump_to_live_r2_execution():
    a = base_agent("L1", "L1", 0.99)
    r = career_cycle_step(a, task("R2"), reward=1)
    assert r["status"] == "REQUIRE_APPROVAL"
    assert r["agent"] == a


def test_l2_promotion_requires_local_competency_evidence_too():
    a = base_agent("L2", "L1", 0.99, xp=250, cases=40)
    r = career_cycle_step(a, task("R1"), reward=1)
    assert r["status"] == "REQUIRE_APPROVAL"
    assert r["agent"] == a


def test_shadow_exploration_is_allowed_without_live_side_effect():
    a = base_agent("L2", "L2", 0.90, xp=60, cases=12)
    r = career_cycle_step(a, task("R1", exploration_mode="SHADOW"), reward=1)
    assert r["status"] == "COMPLETED"
    assert r["exploration"] == "EXPLORE_SHADOW"


def test_error_can_trigger_probation():
    a = base_agent("L3", "L3", 0.61, xp=250, cases=40)
    r1 = career_cycle_step(a, task("R0"), reward=-2, successful=False)
    assert r1["agent"]["career"]["probation"] is True
    assert r1["agent"]["competencies"]["general"]["probation"] is True


def test_probation_reduces_followup_autonomy():
    a = base_agent("L3", "L3", 0.95, xp=250, cases=40)
    a["career"]["probation"] = True
    r = career_cycle_step(a, task("R1"), reward=1)
    assert r["status"] == "REQUIRE_APPROVAL"


def test_recovery_is_possible_after_sustained_success():
    s = {
        "level":"L3","xp":300,"cases":50,"trust":0.85,
        "complex_cases":4,"recent_significant_errors":2,"probation":True
    }
    s2 = recover_significant_errors(s, successful_cases=5)
    assert s2["recent_significant_errors"] == 1
    s3 = recover_significant_errors(s2, successful_cases=5)
    assert s3["recent_significant_errors"] == 0
    assert s3["probation"] is False


def test_recovery_does_not_raise_level_by_itself():
    s = {
        "level":"L2","xp":400,"cases":60,"trust":0.95,
        "complex_cases":6,"recent_significant_errors":1,"probation":True
    }
    s2 = recover_significant_errors(s, successful_cases=5)
    assert s2["level"] == "L2"


def test_mission_never_promotes_when_evidence_is_sufficient():
    a = base_agent("L1", "L1", 0.99, xp=50, cases=10)
    r = career_cycle_step(
        a, task("R0"), reward=1,
        recommendation_detected_at="2026-08-14T10:00:00Z",
    )
    assert r["agent"]["career"]["level"] == "L1"
    assert r["agent"]["competencies"]["general"]["level"] == "L1"
    assert r["meta"]["review_required"] is True


def test_career_and_competency_recommendations_are_distinct_and_notify_once():
    a = base_agent("L1", "L1", 0.99, xp=50, cases=10)
    r = career_cycle_step(
        a, task("R0"), reward=1,
        recommendation_detected_at="2026-08-14T10:00:00Z",
    )
    expected = [
        "promotion:career:global:L1:L2",
        "promotion:competency:sha256-0feae16d55365acf07fe9f909834361ba6ee606854746539230bdc84a6a24cee:L1:L2",
    ]
    assert r["agent"]["career"]["level"] == "L1"
    assert r["agent"]["competencies"]["general"]["level"] == "L1"
    assert r["meta"] == {"review_required": True, "new_recommendations": expected}
    assert [
        item["id"] for item in r["agent"]["evaluations"]["promotion_recommendations"]
    ] == expected

    replay = career_cycle_step(
        r["agent"], task("R0"), reward=1,
        recommendation_detected_at="2026-08-15T10:00:00Z",
    )
    assert replay["meta"] == {"review_required": True, "new_recommendations": []}
    assert len(replay["agent"]["evaluations"]["promotion_recommendations"]) == 2
    assert replay["agent"]["career"]["level"] == "L1"
    assert replay["agent"]["competencies"]["general"]["level"] == "L1"


def test_cycle_rejects_malformed_recommendations_before_recording_outcome():
    agent = base_agent()
    agent["evaluations"] = {"promotion_recommendations": None}
    before = deepcopy(agent)

    with pytest.raises(InvalidPromotionRecommendationStateError):
        career_cycle_step(agent, task("R0"), reward=1)

    assert agent == before


@pytest.mark.parametrize(("field", "value"), [
    ("cases", True),
    ("xp", -1),
    ("trust", False),
])
def test_cycle_rejects_invalid_progression_before_recording_outcome(field, value):
    agent = base_agent()
    agent["career"][field] = value
    before = deepcopy(agent)

    with pytest.raises(InvalidPromotionRecommendationStateError):
        career_cycle_step(agent, task("R0"), reward=1)

    assert agent == before
    assert agent["career"]["level"] == "L1"
