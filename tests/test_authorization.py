from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from aef.authorization import execution_permission


def test_expert_high_trust_low_risk_can_act():
    assert execution_permission(global_level='L5', competency_level='L5', trust=.99, risk='R1') == 'ALLOW'


def test_expert_global_new_competency_cannot_borrow_global_status():
    assert execution_permission(global_level='L5', competency_level='L1', trust=.99, risk='R1') == 'REQUIRE_APPROVAL'


def test_unproven_trust_blocks_nontrivial_execution():
    assert execution_permission(global_level='L3', competency_level='L3', trust=None, risk='R2') == 'REQUIRE_APPROVAL'


def test_probation_reduces_effective_autonomy():
    assert execution_permission(global_level='L5', competency_level='L5', trust=.99, risk='R1', probation=True) == 'REQUIRE_APPROVAL'


def test_hard_policy_always_wins():
    assert execution_permission(global_level='L5', competency_level='L5', trust=.99, risk='R0', hard_effect='deny') == 'DENY'


def test_r4_still_requires_human_approval_for_expert():
    assert execution_permission(global_level='L5', competency_level='L5', trust=.99, risk='R4') == 'REQUIRE_APPROVAL'
