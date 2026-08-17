
from copy import deepcopy
from .progression import record_outcome, promotion_readiness, apply_probation_policy
from .policy import supervision_required, exploration_decision
from .authorization import execution_permission
from .promotion_recommendations import (
    empty_evaluations, ensure_pending_promotion, recommendation_metadata, utc_now,
    validate_progression_snapshot, validate_promotion_recommendation_state,
)
from .identifiers import validate_competency_id


def recover_significant_errors(state, *, successful_cases, minimum_successes=5):
    out = deepcopy(state)
    if successful_cases >= minimum_successes and out.get("recent_significant_errors", 0) > 0:
        out["recent_significant_errors"] = max(0, out["recent_significant_errors"] - 1)
    _, out = apply_probation_policy(out)
    return out


def career_cycle_step(agent, task, *, reward=1, successful=True,
                      successful_recovery_cases=0, recommendation_detected_at=None):
    """Reference career-cycle step used only by the lab.

    It composes execution authorization, outcome learning, probation/recovery,
    promotion and post-task supervision.
    """
    before = deepcopy(agent)
    validate_competency_id(task["competency"])
    competency = before["competencies"][task["competency"]]
    if "evaluations" in before:
        validate_promotion_recommendation_state(before["evaluations"])
    validate_progression_snapshot(before["career"])
    validate_progression_snapshot(competency)

    permission = execution_permission(
        global_level=before["career"]["level"],
        competency_level=competency["level"],
        trust=competency.get("trust"),
        risk=task["risk"],
        probation=before["career"].get("probation", False) or competency.get("probation", False),
        hard_effect=task.get("hard_effect"),
        irreversible=task.get("irreversible", False),
    )
    if permission in {"DENY", "ESCALATE", "REQUIRE_APPROVAL"}:
        return {
            "status": permission,
            "agent": before,
            "permission": permission,
            "supervision_required": True,
            "exploration": None,
        }

    exploration = None
    if task.get("exploration_mode"):
        exploration = exploration_decision(
            mode=task["exploration_mode"],
            risk=task["risk"],
            reversible=task.get("reversible", False),
            hard_block=task.get("hard_block", False),
            requires_approval=task.get("requires_approval", False),
        )
        if exploration in {"DENY_EXPLORATION", "REQUIRE_APPROVAL"}:
            return {
                "status": exploration,
                "agent": before,
                "permission": permission,
                "supervision_required": True,
                "exploration": exploration,
            }

    updated = deepcopy(before)
    updated["career"] = record_outcome(updated["career"], difficulty=task["difficulty"],
                                       reward=reward, successful=successful)
    updated["competencies"][task["competency"]] = record_outcome(
        updated["competencies"][task["competency"]],
        difficulty=task["difficulty"], reward=reward, successful=successful
    )

    if successful_recovery_cases:
        updated["career"] = recover_significant_errors(
            updated["career"], successful_cases=successful_recovery_cases
        )
        updated["competencies"][task["competency"]] = recover_significant_errors(
            updated["competencies"][task["competency"]],
            successful_cases=successful_recovery_cases
        )
    else:
        _, updated["career"] = apply_probation_policy(updated["career"])
        _, updated["competencies"][task["competency"]] = apply_probation_policy(
            updated["competencies"][task["competency"]]
        )

    career_readiness = promotion_readiness(updated["career"])
    competency_state = updated["competencies"][task["competency"]]
    competency_readiness = promotion_readiness(competency_state)
    evaluations = deepcopy(updated.get("evaluations", empty_evaluations()))
    detected_at = recommendation_detected_at
    if detected_at is None and (career_readiness["eligible"] or competency_readiness["eligible"]):
        detected_at = utc_now()
    new_recommendations = []
    _, evaluations, recommendation_id, created = ensure_pending_promotion(
        evaluations, updated["career"], scope="career", competency_id=None,
        detected_at=detected_at,
    )
    if created:
        new_recommendations.append(recommendation_id)
    _, evaluations, recommendation_id, created = ensure_pending_promotion(
        evaluations, competency_state, scope="competency", competency_id=task["competency"],
        detected_at=detected_at,
    )
    if created:
        new_recommendations.append(recommendation_id)
    updated["evaluations"] = evaluations

    supervision = supervision_required(
        updated["career"]["level"],
        updated["competencies"][task["competency"]]["level"],
        incident=reward <= -2,
    )

    return {
        "status": "COMPLETED",
        "agent": updated,
        "permission": permission,
        "supervision_required": supervision,
        "exploration": exploration,
        "career_readiness": career_readiness,
        "competency_readiness": competency_readiness,
        "meta": recommendation_metadata(evaluations, new_recommendations),
    }
