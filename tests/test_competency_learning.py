from copy import deepcopy
import pytest
from aef.competency_learning import ensure_competency, propose_transfer, seed_transfer_hypothesis
from aef.authorization import execution_permission
from aef.career_cycle import career_cycle_step
from aef.identifiers import InvalidCompetencyIdentifierError, validate_competency_id


def expert_agent():
    return {
        "career": {"level":"L5","xp":1500,"cases":220,"trust":0.99,"complex_cases":40,"recent_significant_errors":0,"probation":False},
        "competencies": {
            "writing": {"id":"writing","level":"L5","xp":1200,"cases":180,"trust":0.99,"complex_cases":30,"recent_significant_errors":0,"probation":False}
        }
    }


def test_unknown_competency_is_born_at_l1_unproven():
    status, agent = ensure_competency(expert_agent(), "contract-analysis")
    c = agent["competencies"]["contract-analysis"]
    assert status == "CHANGE"
    assert c["level"] == "L1"
    assert c["trust"] is None
    assert c["xp"] == 0 and c["cases"] == 0


def test_competency_creation_accepts_colon_for_new_identifier():
    source = expert_agent()
    status, out = ensure_competency(source, "a:b")

    assert status == "CHANGE"
    assert out["competencies"]["a:b"]["id"] == "a:b"


@pytest.mark.parametrize("legacy_id", ["a:b"])
def test_existing_legacy_competency_is_exact_no_change_before_new_id_validation(legacy_id):
    source = expert_agent()
    source["competencies"][legacy_id] = {"id": legacy_id, "opaque": {"kept": True}}
    before = deepcopy(source)

    status, out = ensure_competency(source, legacy_id)

    assert status == "NO_CHANGE"
    assert out == before
    assert source == before
    assert list(out["competencies"])[-1] == legacy_id


@pytest.mark.parametrize("invalid_id", ["", "   ", " legacy ", "legacy\u0007id"])
def test_existing_invalid_competency_is_quarantined_without_normalization(invalid_id):
    source = expert_agent()
    source["competencies"][invalid_id] = {
        "id": invalid_id, "level": "L1", "xp": 0, "cases": 0,
        "trust": None, "complex_cases": 0, "recent_significant_errors": 0,
    }
    before = deepcopy(source)

    with pytest.raises(InvalidCompetencyIdentifierError, match="explicit migration required"):
        ensure_competency(source, invalid_id)

    assert source == before
    assert invalid_id in source["competencies"]


def test_invalid_existing_competency_cannot_progress_or_recommend():
    source = expert_agent()
    invalid_id = " legacy "
    source["competencies"][invalid_id] = {
        "id": invalid_id, "level": "L1", "xp": 50, "cases": 10,
        "trust": 0.90, "complex_cases": 0, "recent_significant_errors": 0,
        "probation": False,
    }
    before = deepcopy(source)

    with pytest.raises(InvalidCompetencyIdentifierError, match="explicit migration required"):
        career_cycle_step(
            source,
            {"competency": invalid_id, "risk": "R0", "difficulty": "D1"},
            reward=2,
            recommendation_detected_at="2026-08-14T10:00:00Z",
        )

    assert source == before
    assert "evaluations" not in source
    assert source["competencies"][invalid_id]["level"] == "L1"
    assert source["competencies"][invalid_id]["cases"] == 10


def test_all_inventoried_repository_competency_identifiers_remain_valid():
    inventoried = {
        "analysis", "contract-analysis", "database-admin", "general",
        "record-classification", "technical-writing", "triage", "writing",
    }

    assert {validate_competency_id(value) for value in inventoried} == inventoried


@pytest.mark.parametrize("competency_id", [
    " leading", "trailing ", "\tcontrol", "line\nbreak",
    "dry\u00a0run", "caf\u0435", "review\u200b-skill",
])
def test_new_competency_rejects_edge_spaces_and_unicode_controls(competency_id):
    source = expert_agent()
    before = deepcopy(source)

    with pytest.raises(InvalidCompetencyIdentifierError):
        ensure_competency(source, competency_id)

    assert source == before


@pytest.mark.parametrize("competency_id", ["日本語", "domain/subdomain", r"domain\subdomain", "a:b"])
def test_new_competency_accepts_unicode_and_non_path_separators(competency_id):
    status, out = ensure_competency(expert_agent(), competency_id)

    assert status == "CHANGE"
    assert out["competencies"][competency_id]["id"] == competency_id


def test_ensure_competency_is_idempotent_and_preserves_existing_state():
    status, a1 = ensure_competency(expert_agent(), "contract-analysis")
    a1["competencies"]["contract-analysis"]["xp"] = 12
    status2, a2 = ensure_competency(a1, "contract-analysis")
    assert status2 == "NO_CHANGE"
    assert a2["competencies"]["contract-analysis"]["xp"] == 12


def test_global_expert_does_not_grant_new_competency_autonomy():
    _, a = ensure_competency(expert_agent(), "contract-analysis")
    c = a["competencies"]["contract-analysis"]
    decision = execution_permission(global_level=a["career"]["level"], competency_level=c["level"], trust=c["trust"], risk="R2")
    assert decision == "REQUIRE_APPROVAL"


def test_learning_new_competency_does_not_mutate_existing_competency():
    _, a = ensure_competency(expert_agent(), "contract-analysis")
    writing_before = deepcopy(a["competencies"]["writing"])
    r = career_cycle_step(a, {"competency":"contract-analysis","risk":"R0","difficulty":"D2"}, reward=1)
    assert r["status"] == "COMPLETED"
    assert r["agent"]["competencies"]["writing"] == writing_before
    assert r["agent"]["competencies"]["contract-analysis"]["cases"] == 1


def test_near_skill_transfer_is_hint_not_copied_maturity():
    _, a = ensure_competency(expert_agent(), "technical-writing")
    p = propose_transfer(a, "writing", "technical-writing", similarity=0.80)
    assert p["status"] == "TRANSFER_HINT"
    assert p["effects"]["copy_level"] is False
    assert p["effects"]["copy_trust"] is False
    assert a["competencies"]["technical-writing"]["level"] == "L1"
    assert a["competencies"]["technical-writing"]["trust"] is None


def test_low_similarity_transfer_is_rejected():
    _, a = ensure_competency(expert_agent(), "database-admin")
    p = propose_transfer(a, "writing", "database-admin", similarity=0.20)
    assert p["status"] == "REJECT_TRANSFER"


def test_transfer_hypothesis_is_stable_and_target_unvalidated():
    records = []
    status, records = seed_transfer_hypothesis(records, source_competency="writing", target_competency="technical-writing", statement="Clear structure may improve technical explanations")
    status2, records2 = seed_transfer_hypothesis(records, source_competency="writing", target_competency="technical-writing", statement="Clear structure may improve technical explanations")
    assert status == "CHANGE"
    assert status2 == "NO_CHANGE"
    assert len(records2) == 1
    assert records2[0]["validated_in_target"] is False


def test_new_competency_creates_recommendation_without_changing_levels():
    _, a = ensure_competency(expert_agent(), "contract-analysis")
    original_global = deepcopy(a["career"])
    # Ten safe R0/D3 cases are enough for the default L1->L2 evidence gate.
    for _ in range(10):
        r = career_cycle_step(
            a, {"competency":"contract-analysis","risk":"R0","difficulty":"D3"}, reward=1,
            recommendation_detected_at="2026-08-14T10:00:00Z",
        )
        assert r["status"] == "COMPLETED"
        a = r["agent"]
    c = a["competencies"]["contract-analysis"]
    assert c["level"] == "L1"
    assert c["cases"] == 10
    assert c["trust"] >= 0.80
    # Global expert identity was not mutated by local competency learning.
    assert a["career"]["level"] == original_global["level"] == "L5"
    assert [
        item["id"] for item in a["evaluations"]["promotion_recommendations"]
    ] == ["promotion:competency:sha256-31792814ec133ce9f987681d3c9111928c6bde3653da2ec6d1de7ca4d9c456c8:L1:L2"]
    # A recommendation is not approval and does not unlock R1.
    decision = execution_permission(global_level=a["career"]["level"], competency_level=c["level"], trust=c["trust"], risk="R1")
    assert decision == "REQUIRE_APPROVAL"


def test_transfer_hint_never_shortcuts_target_evidence_gate():
    _, a = ensure_competency(expert_agent(), "technical-writing")
    p = propose_transfer(a, "writing", "technical-writing", similarity=0.95)
    assert p["status"] == "TRANSFER_HINT"
    c = a["competencies"]["technical-writing"]
    assert execution_permission(global_level="L5", competency_level=c["level"], trust=c["trust"], risk="R1") == "REQUIRE_APPROVAL"
