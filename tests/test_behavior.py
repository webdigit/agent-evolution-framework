import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aef.policy import supervision_required, should_ask_for_help, exploration_decision
from aef.migrations import apply_migration


def test_l1_is_supervised_after_each_task():
    assert supervision_required("L1", "L1") is True


def test_expert_global_but_new_competency_is_supervised():
    assert supervision_required("L5", "L1") is True


def test_expert_mature_competency_not_supervised_each_task():
    assert supervision_required("L5", "L5") is False


def test_incident_forces_supervision_at_any_level():
    assert supervision_required("L5", "L5", incident=True) is True


def test_help_requested_for_missing_material_information():
    assert should_ask_for_help(confidence=0.99, risk="R1", competency_level="L5", missing_material_info=True)


def test_help_requested_for_low_confidence_relative_to_maturity():
    assert should_ask_for_help(confidence=0.60, risk="R1", competency_level="L3")


def test_expert_does_not_over_escalate_trivial_high_confidence_case():
    assert not should_ask_for_help(confidence=0.95, risk="R0", competency_level="L5")


def test_shadow_and_simulation_are_safe_exploration_modes():
    assert exploration_decision(mode="SHADOW", risk="R4", reversible=False) == "EXPLORE_SHADOW"
    assert exploration_decision(mode="SIMULATE", risk="R4", reversible=False) == "EXPLORE_SIMULATE"


def test_live_r4_exploration_requires_approval():
    assert exploration_decision(mode="LIVE", risk="R4", reversible=False) == "REQUIRE_APPROVAL"


def test_low_risk_reversible_live_exploration_can_be_allowed():
    assert exploration_decision(mode="LIVE", risk="R1", reversible=True) == "EXPLORE_LIVE"


def test_hard_policy_overrides_exploration():
    assert exploration_decision(mode="LIVE", risk="R1", reversible=True, hard_block=True) == "DENY_EXPLORATION"


def test_migration_is_replay_safe():
    state = {"schema_version": "1.0"}
    ledger = {"applied": []}

    def upgrade(s):
        s["schema_version"] = "1.1"
        return s

    status1, s1, l1 = apply_migration(state, ledger, "mig-1.0-1.1", "1.0", "1.1", upgrade)
    status2, s2, l2 = apply_migration(s1, l1, "mig-1.0-1.1", "1.0", "1.1", upgrade)
    assert status1 == "CHANGE"
    assert status2 == "NO_CHANGE"
    assert s1 == s2
    assert l1 == l2
