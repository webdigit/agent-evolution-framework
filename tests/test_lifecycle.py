from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aef.lifecycle import handle_task
from aef.progression import promotion_readiness, promote_if_eligible, record_outcome


def base_agent():
    return {
        "career": {"level":"L1","xp":0,"trust":None,"cases":0,"complex_cases":0,"recent_significant_errors":0,"probation":False},
        "competencies": {
            "triage": {"level":"L1","xp":0,"trust":None,"cases":0,"complex_cases":0,"recent_significant_errors":0,"probation":False}
        }
    }


def task(**overrides):
    x = {"competency":"triage","difficulty":"D2","risk":"R0","confidence":0.95,"reversible":True}
    x.update(overrides)
    return x


def test_l1_successful_task_requires_supervision_and_learns():
    out = handle_task(base_agent(), task(), reward=2)
    assert out["status"] == "COMPLETED"
    assert out["supervision_required"] is True
    assert out["agent"]["career"]["xp"] == 4
    assert out["agent"]["career"]["trust"] is not None


def test_help_request_does_not_mutate_progression():
    a = base_agent()
    out = handle_task(a, task(missing_material_info=True), reward=2)
    assert out["status"] == "ASK_FOR_HELP"
    assert out["agent"] == a


def test_shadow_exploration_can_complete_without_live_risk():
    out = handle_task(base_agent(), task(exploration_mode="SHADOW", risk="R4", reversible=False), reward=1)
    assert out["status"] == "COMPLETED"
    assert out["exploration"] == "EXPLORE_SHADOW"


def test_live_r4_exploration_stops_before_mutating_state():
    a = base_agent()
    out = handle_task(a, task(exploration_mode="LIVE", risk="R4", reversible=False), reward=1)
    assert out["status"] == "REQUIRE_APPROVAL"
    assert out["agent"] == a


def test_significant_error_triggers_supervision_and_can_trigger_probation():
    a = base_agent()
    out1 = handle_task(a, task(), reward=-2, successful=False)
    out2 = handle_task(out1["agent"], task(), reward=-2, successful=False)
    assert out2["supervision_required"] is True
    assert out2["agent"]["career"]["probation"] is True


def test_promotion_requires_multi_dimensional_evidence():
    s = {"level":"L1","xp":60,"trust":0.90,"cases":9,"complex_cases":0,"recent_significant_errors":0,"probation":False}
    readiness = promotion_readiness(s)
    assert readiness["eligible"] is False
    assert any(x.startswith("cases:") for x in readiness["reasons"])


def test_promotion_can_happen_after_thresholds_and_human_approval():
    s = {"level":"L1","xp":60,"trust":0.90,"cases":10,"complex_cases":0,"recent_significant_errors":0,"probation":False}
    status, promoted, readiness = promote_if_eligible(s, human_approval=True)
    assert readiness["eligible"] is True
    assert status == "CHANGE"
    assert promoted["level"] == "L2"


def test_promotion_can_be_held_for_human_review():
    s = {"level":"L1","xp":60,"trust":0.90,"cases":10,"complex_cases":0,"recent_significant_errors":0,"probation":False}
    status, promoted, readiness = promote_if_eligible(s, human_approval=False)
    assert readiness["eligible"] is True
    assert status == "NO_CHANGE"
    assert promoted["level"] == "L1"


def test_direct_promotion_requires_explicit_approval_argument():
    state = {"level":"L1","xp":60,"trust":0.90,"cases":10,"complex_cases":0,"recent_significant_errors":0,"probation":False}

    with pytest.raises(TypeError):
        promote_if_eligible(state)


def test_failed_well_designed_experiment_need_not_be_critical_error():
    s = {"level":"L3","xp":200,"trust":0.92,"cases":30,"complex_cases":0,"recent_significant_errors":0,"probation":False}
    after = record_outcome(s, difficulty="D2", reward=0, successful=False)
    assert after["recent_significant_errors"] == 0
    assert after["trust"] == 0.92


def test_new_competency_remains_locally_junior_even_if_global_is_expert():
    a = base_agent()
    a["career"].update({"level":"L5","xp":1500,"trust":0.99,"cases":200,"complex_cases":50})
    out = handle_task(a, task(), reward=1)
    assert out["supervision_required"] is True


def test_handle_task_recommends_but_never_promotes():
    a = base_agent()
    a["career"].update({"xp": 50, "cases": 10, "trust": 0.90})
    a["competencies"]["triage"].update({"xp": 50, "cases": 10, "trust": 0.90})

    out = handle_task(
        a, task(), reward=1,
        recommendation_detected_at="2026-08-14T10:00:00Z",
    )

    assert out["agent"]["career"]["level"] == "L1"
    assert out["agent"]["competencies"]["triage"]["level"] == "L1"
    assert out["meta"] == {
        "review_required": True,
        "new_recommendations": [
            "promotion:career:global:L1:L2",
            "promotion:competency:sha256-0f916789d300a986c41cdb23c248926aaec1f73a9bbae0a7efae074f16480baf:L1:L2",
        ],
    }
