from copy import deepcopy

from aef.operations import init_project, audit_project, consolidate_knowledge
from aef.career_cycle import career_cycle_step
from aef.competency_learning import ensure_competency
from aef.learning_lifecycle import observe, derive_hypothesis, confirm_hypothesis, derive_rule
from aef.release import apply_framework_release


def blank_project():
    return {"files": {"notes/project-owned.md": "keep me"}}


def novice_agent():
    return {
        "career": {"level":"L1","xp":0,"cases":0,"trust":None,"complex_cases":0,"recent_significant_errors":0,"probation":False},
        "competencies": {}
    }


def migration(mid, f, t, key):
    def transform(project):
        project = deepcopy(project)
        project.setdefault("files", {})[f".agent/state/{key}.json"] = {"version": t}
        return project
    return {"id": mid, "from_version": f, "to_version": t, "transform": transform,
            "postcondition": lambda p: p.get("files", {}).get(f".agent/state/{key}.json", {}).get("version") == t}


def test_birth_requires_durable_role_decision_and_is_replay_safe():
    p0 = blank_project()
    status, blocked, meta = init_project(p0, instance_id="synthetic-agent-001", required_decisions=["decision.role.primary.v1"])
    assert status == "BLOCKED"
    assert blocked == p0
    assert meta["unresolved_decisions"] == ["decision.role.primary.v1"]

    status, p1, _ = init_project(
        p0, instance_id="synthetic-agent-001",
        answers={"decision.role.primary.v1":"generalist-agent"},
        required_decisions=["decision.role.primary.v1"], created_at="2026-08-13T00:00:00Z"
    )
    assert status == "CHANGE"
    assert p1["files"]["notes/project-owned.md"] == "keep me"
    status2, p2, _ = init_project(
        p1, instance_id="synthetic-agent-001",
        required_decisions=["decision.role.primary.v1"], created_at="2026-08-13T00:00:00Z"
    )
    assert status2 == "NO_CHANGE"
    assert p2 == p1


def test_novice_learning_recommends_promotion_but_does_not_unlock_local_r1():
    a = novice_agent()
    _, a = ensure_competency(a, "record-classification", title="Record classification")
    for _ in range(10):
        r = career_cycle_step(
            a, {"competency":"record-classification","risk":"R0","difficulty":"D3"}, reward=1,
            recommendation_detected_at="2026-08-14T10:00:00Z",
        )
        assert r["status"] == "COMPLETED"
        assert r["supervision_required"] is True or r["agent"]["competencies"]["record-classification"]["level"] == "L2"
        a = r["agent"]
    assert a["career"]["level"] == "L1"
    assert a["competencies"]["record-classification"]["level"] == "L1"
    assert a["competencies"]["record-classification"]["trust"] >= 0.80
    r1 = career_cycle_step(a, {"competency":"record-classification","risk":"R1","difficulty":"D2"}, reward=1)
    assert r1["status"] == "REQUIRE_APPROVAL"
    assert {
        item["id"] for item in a["evaluations"]["promotion_recommendations"]
    } == {
        "promotion:career:global:L1:L2",
        "promotion:competency:sha256-0fc7ee538068e4090d67e516131c7e36d2e5fe3886570753a75912d085b69396:L1:L2",
    }


def test_incident_then_recovery_does_not_erase_history():
    a = novice_agent()
    _, a = ensure_competency(a, "record-classification")
    # Build enough safe evidence to reach L2.
    for _ in range(10):
        a = career_cycle_step(a, {"competency":"record-classification","risk":"R0","difficulty":"D3"}, reward=1)["agent"]
    before_cases = a["competencies"]["record-classification"]["cases"]
    # Two significant R0 mistakes trigger probation while remaining executable.
    for _ in range(2):
        r = career_cycle_step(a, {"competency":"record-classification","risk":"R0","difficulty":"D2"}, reward=-2, successful=False)
        a = r["agent"]
    assert a["competencies"]["record-classification"]["probation"] is True
    assert a["competencies"]["record-classification"]["cases"] == before_cases + 2
    # Recovery requires sustained successful evidence; history stays.
    for _ in range(2):
        r = career_cycle_step(a, {"competency":"record-classification","risk":"R0","difficulty":"D2"}, reward=2, successful=True, successful_recovery_cases=5)
        a = r["agent"]
    assert a["competencies"]["record-classification"]["recent_significant_errors"] == 0
    assert a["competencies"]["record-classification"]["probation"] is False
    assert a["competencies"]["record-classification"]["cases"] >= before_cases + 4


def test_observations_can_become_rule_but_not_principle_implicitly():
    observations, hypotheses, rules = [], [], []
    for i in (1, 2):
        _, observations = observe(observations, observation_id=f"obs-{i}", summary="Ambiguous records need source verification", pattern_key="verify-ambiguous-source")
    status, hypotheses, hid = derive_hypothesis(observations, hypotheses, pattern_key="verify-ambiguous-source")
    assert status == "CHANGE" and hid
    for _ in range(3):
        _, hypotheses = confirm_hypothesis(hypotheses, hid)
    status, rules, rid = derive_rule(hypotheses, rules, hypothesis_id=hid)
    assert status == "CHANGE" and rid
    assert rules[0]["status"] == "active"


def test_consolidation_specializes_rule_without_deleting_history():
    state = {"rules":[{"id":"rule:verify-source","type":"rule","status":"active","pattern_key":"verify-source"}]}
    status, out, decisions = consolidate_knowledge(state, rule_reviews=[{
        "rule_id":"rule:verify-source", "contradictions":1,
        "contexts":[{"record_type":"ambiguous"}], "reason":"Only ambiguous records require extra source verification",
        "evidence_ids":["obs-a","obs-b"]
    }])
    assert status == "CHANGE"
    assert len(out["rules"]) == 1
    assert out["rules"][0]["status"] == "specialized"
    assert out["rules"][0]["context"] == {"record_type":"ambiguous"}
    assert decisions[0]["decision"] == "SPECIALIZE"


def test_audit_consolidate_upgrade_full_lifecycle_preserves_project_content():
    _, p, _ = init_project(
        blank_project(), instance_id="synthetic-agent-001",
        answers={"decision.role.primary.v1":"generalist-agent"},
        required_decisions=["decision.role.primary.v1"]
    )
    audit_before = audit_project(p)
    snapshot = deepcopy(p)
    audit_again = audit_project(p)
    assert audit_before == audit_again
    assert p == snapshot

    migrations = [
        migration("schema-100-110", "1.0.0", "1.1.0", "evaluation"),
        migration("schema-110-120", "1.1.0", "1.2.0", "learning-signals"),
    ]
    status, upgraded, meta = apply_framework_release(
        p, target_version="1.2.0", migrations=migrations,
        managed_updates={".agent/core/learning.md":"# AEF Learning v1.2\n"}
    )
    assert status == "CHANGE"
    assert upgraded["files"][".agent/manifest.json"]["schema_version"] == "1.2.0"
    assert upgraded["files"]["notes/project-owned.md"] == "keep me"
    assert meta["phase"] == "complete"
    # A replay at target is a no-op.
    status2, upgraded2, meta2 = apply_framework_release(
        upgraded, target_version="1.2.0", migrations=migrations,
        managed_updates={".agent/core/learning.md":"# AEF Learning v1.2\n"}
    )
    assert status2 == "NO_CHANGE"
    assert upgraded2 == upgraded
    assert meta2["phase"] == "complete"
