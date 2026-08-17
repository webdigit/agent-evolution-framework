from copy import deepcopy
from .policy import supervision_required, should_ask_for_help, exploration_decision
from .progression import record_outcome, promotion_readiness, apply_probation_policy
from .promotion_recommendations import (
    empty_evaluations, ensure_pending_promotion, recommendation_metadata, utc_now,
    validate_progression_snapshot, validate_promotion_recommendation_state,
)
from .identifiers import validate_competency_id


def handle_task(agent, task, *, reward=1, successful=True, recommendation_detected_at=None):
    """Deterministic reference lifecycle for lab scenarios.

    This is intentionally small: it demonstrates how governance components
    compose without pretending to be a full production orchestrator.
    """
    before = deepcopy(agent)
    validate_competency_id(task["competency"])
    competency = agent["competencies"][task["competency"]]
    if "evaluations" in before:
        validate_promotion_recommendation_state(before["evaluations"])
    validate_progression_snapshot(before["career"])
    validate_progression_snapshot(competency)

    help_needed = should_ask_for_help(
        confidence=task["confidence"],
        risk=task["risk"],
        competency_level=competency["level"],
        missing_material_info=task.get("missing_material_info", False),
        conflicting_sources=task.get("conflicting_sources", False),
        irreversible=task.get("irreversible", False),
        tool_anomaly=task.get("tool_anomaly", False),
    )

    if help_needed:
        return {
            "status": "ASK_FOR_HELP",
            "agent": before,
            "supervision_required": True,
            "exploration": None,
        }

    exp = None
    if task.get("exploration_mode"):
        exp = exploration_decision(
            mode=task["exploration_mode"], risk=task["risk"],
            reversible=task.get("reversible", False),
            hard_block=task.get("hard_block", False),
            requires_approval=task.get("requires_approval", False),
        )
        if exp in {"REQUIRE_APPROVAL", "DENY_EXPLORATION"}:
            return {
                "status": exp,
                "agent": before,
                "supervision_required": True,
                "exploration": exp,
            }

    updated = deepcopy(agent)
    updated["career"] = record_outcome(updated["career"], difficulty=task["difficulty"], reward=reward, successful=successful)
    updated["competencies"][task["competency"]] = record_outcome(competency, difficulty=task["difficulty"], reward=reward, successful=successful)

    _, updated["career"] = apply_probation_policy(updated["career"])
    _, updated["competencies"][task["competency"]] = apply_probation_policy(updated["competencies"][task["competency"]])

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
        "supervision_required": supervision,
        "exploration": exp,
        "career_readiness": career_readiness,
        "competency_readiness": competency_readiness,
        "meta": recommendation_metadata(evaluations, new_recommendations),
    }
