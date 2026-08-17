from copy import deepcopy

from .identifiers import InvalidCompetencyIdentifierError, validate_competency_id
from .progression import promotion_readiness
from .progression import promote_if_eligible
from .promotion_recommendations import (
    InvalidPromotionRecommendationStateError,
    LEVEL_ORDER,
    _evidence_digest,
    _evidence_snapshot,
    _valid_rfc3339,
    validate_evaluation_state,
    validate_progression_snapshot,
)
from .strict_json import InvalidStrictJSONError, validate_strict_json
from .transaction_guard import mutation_guard_metadata


class InvalidEvaluationDecisionsError(ValueError):
    """Raised when an explicit EVALUATE decision document is invalid."""


class InvalidCareerStateError(ValueError):
    """Raised when persisted career state cannot safely be evaluated."""


class InvalidCompetencyStateError(ValueError):
    """Raised when persisted competency state cannot safely be evaluated."""


APPROVE_FIELDS = {
    "id", "recommendation_id", "decision", "reason",
    "expected_evidence_digest", "expected_current_evidence_digest", "approval",
}
REJECT_FIELDS = {
    "id", "recommendation_id", "decision", "reason",
    "expected_evidence_digest", "rejection",
}
EVALUATIONS_PATH = ".agent/state/evaluations.json"
CAREER_PATH = ".agent/state/career.json"
COMPETENCIES_PATH = ".agent/state/competencies.json"
HUMAN_APPROVAL_FIELDS = {"approved", "source", "actor", "approved_at"}
HUMAN_REJECTION_FIELDS = {"rejected", "source", "actor", "rejected_at"}


def _non_empty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_human_record(record, *, decision):
    if decision == "approve":
        fields = HUMAN_APPROVAL_FIELDS
        flag, timestamp = "approved", "approved_at"
    else:
        fields = HUMAN_REJECTION_FIELDS
        flag, timestamp = "rejected", "rejected_at"
    if not isinstance(record, dict) or set(record) != fields:
        raise InvalidEvaluationDecisionsError("invalid human decision record")
    if record[flag] is not True or record["source"] != "human":
        raise InvalidEvaluationDecisionsError("explicit human decision required")
    if not _non_empty_text(record["actor"]):
        raise InvalidEvaluationDecisionsError("invalid human decision actor")
    if not _valid_rfc3339(record[timestamp]):
        raise InvalidEvaluationDecisionsError("invalid human decision timestamp")


def validate_evaluation_decisions(document):
    """Validate the closed ``aef.evaluate/v1`` batch without mutation."""
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidEvaluationDecisionsError("invalid strict JSON") from exc
    if not isinstance(document, dict) or set(document) != {"protocol", "decisions"}:
        raise InvalidEvaluationDecisionsError("invalid evaluation decision document")
    if document["protocol"] != "aef.evaluate/v1" or not isinstance(
        document["decisions"], list
    ):
        raise InvalidEvaluationDecisionsError("invalid evaluation decision protocol")
    seen = set()
    for entry in document["decisions"]:
        if not isinstance(entry, dict):
            raise InvalidEvaluationDecisionsError("invalid evaluation decision")
        decision = entry.get("decision")
        expected_fields = APPROVE_FIELDS if decision == "approve" else REJECT_FIELDS
        if decision not in {"approve", "reject"} or set(entry) != expected_fields:
            raise InvalidEvaluationDecisionsError("invalid evaluation decision")
        if not _non_empty_text(entry["id"]) or entry["id"] in seen:
            raise InvalidEvaluationDecisionsError("invalid evaluation decision id")
        seen.add(entry["id"])
        if not _non_empty_text(entry["recommendation_id"]):
            raise InvalidEvaluationDecisionsError("invalid recommendation id")
        if not _non_empty_text(entry["reason"]):
            raise InvalidEvaluationDecisionsError("invalid evaluation decision reason")
        digests = [entry["expected_evidence_digest"]]
        if decision == "approve":
            digests.append(entry["expected_current_evidence_digest"])
        if any(
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or len(digest) != 71
            or any(char not in "0123456789abcdef" for char in digest[7:])
            for digest in digests
        ):
            raise InvalidEvaluationDecisionsError("invalid evaluation evidence digest")
        _validate_human_record(
            entry["approval" if decision == "approve" else "rejection"],
            decision=decision,
        )
    return document


def validate_promotable_career_state(state):
    """Validate all career fields EVALUATE relies on without normalization."""
    try:
        validate_strict_json(state)
        validate_progression_snapshot(state)
    except (InvalidStrictJSONError, ValueError) as exc:
        raise InvalidCareerStateError("invalid career state") from exc
    if state.get("status") not in {"active", "paused", "retired"}:
        raise InvalidCareerStateError("invalid career status")
    if not isinstance(state.get("probation"), bool):
        raise InvalidCareerStateError("invalid career probation")
    return state


def validate_promotable_competency_state(competency_id, state):
    """Validate one canonical competency targeted by EVALUATE."""
    try:
        validate_competency_id(competency_id)
        validate_strict_json(state)
        validate_progression_snapshot(state)
    except (InvalidCompetencyIdentifierError, InvalidStrictJSONError, ValueError) as exc:
        raise InvalidCompetencyStateError("invalid competency state") from exc
    if not isinstance(state.get("probation"), bool):
        raise InvalidCompetencyStateError("invalid competency probation")
    if "id" in state and state["id"] != competency_id:
        raise InvalidCompetencyStateError("incoherent competency identity")
    return state


def promotion_eligibility(state):
    """Shared readiness predicate including the V1 probation invariant."""
    readiness = promotion_readiness(state)
    if state.get("probation") is True:
        reasons = list(readiness["reasons"])
        if "probation" not in reasons:
            reasons.append("probation")
        return {"eligible": False, "target": readiness["target"], "reasons": reasons}
    return readiness


def _current_subject(recommendation, career, competencies):
    if recommendation["scope"] == "career":
        validate_promotable_career_state(career)
        return career, None
    competency_id = recommendation["competency_id"]
    if not isinstance(competencies, dict) or competency_id not in competencies:
        return None, "competency_missing"
    validate_promotable_competency_state(competency_id, competencies[competency_id])
    return competencies[competency_id], None


def _describe_pending(recommendation, career, competencies):
    current, missing_reason = _current_subject(recommendation, career, competencies)
    if current is None:
        return {
            **deepcopy(recommendation),
            "current_level": None,
            "current_evidence": None,
            "current_evidence_digest": None,
            "readiness": {"eligible": False, "target": None, "reasons": [missing_reason]},
            "probation": None,
            "stale": True,
            "stale_reason": missing_reason,
        }
    current_evidence = _evidence_snapshot(current)
    readiness = promotion_eligibility(current)
    if current["level"] != recommendation["from_level"]:
        stale_reason = "source_level_changed"
    elif readiness["target"] != recommendation["to_level"]:
        stale_reason = "target_changed"
    elif current.get("probation") is True:
        stale_reason = "probation"
    elif not readiness["eligible"]:
        stale_reason = "not_eligible"
    else:
        stale_reason = None
    return {
        **deepcopy(recommendation),
        "current_level": current["level"],
        "current_evidence": current_evidence,
        "current_evidence_digest": _evidence_digest(current_evidence),
        "readiness": readiness,
        "probation": current["probation"],
        "stale": stale_reason is not None,
        "stale_reason": stale_reason,
    }


def list_pending_recommendations(evaluations, career, competencies):
    """Return deterministic, freshly assessed pending recommendations."""
    validate_evaluation_state(evaluations)
    pending = [
        _describe_pending(item, career, competencies)
        for item in evaluations.get("promotion_recommendations", [])
        if item["status"] == "pending"
    ]
    scope_order = {"career": 0, "competency": 1}
    return sorted(
        pending,
        key=lambda item: (
            item["detected_at"], scope_order[item["scope"]],
            item["competency_id"] or "", item["id"],
        ),
    )


def _workspace_states(project):
    source = deepcopy(project)
    files = source.get("files")
    manifest = files.get(".agent/manifest.json") if isinstance(files, dict) else None
    if not isinstance(manifest, dict) or manifest.get("framework") != "aef":
        return source, None, None, None
    if EVALUATIONS_PATH not in files:
        raise InvalidPromotionRecommendationStateError("evaluation state is missing")
    if CAREER_PATH not in files:
        raise InvalidCareerStateError("career state is missing")
    if COMPETENCIES_PATH not in files:
        raise InvalidCompetencyStateError("competency state is missing")
    evaluations = files[EVALUATIONS_PATH]
    career = files[CAREER_PATH]
    competencies = files[COMPETENCIES_PATH]
    validate_evaluation_state(evaluations)
    validate_promotable_career_state(career)
    if not isinstance(competencies, dict):
        raise InvalidCompetencyStateError("invalid competency collection")
    return source, evaluations, career, competencies


def list_project_recommendations(project):
    source, evaluations, career, competencies = _workspace_states(project)
    if evaluations is None:
        return "BLOCKED", source, {
            "reason": "workspace_not_initialized", "recommendations": [],
        }
    return "NO_CHANGE", source, {
        "reason": None,
        "recovery_required": mutation_guard_metadata(source) is not None,
        "recommendations": list_pending_recommendations(
            evaluations, career, competencies
        ),
    }


def _persisted_decision(entry, recommendation, description):
    human = entry["approval" if entry["decision"] == "approve" else "rejection"]
    timestamp_field = "approved_at" if entry["decision"] == "approve" else "rejected_at"
    return {
        "id": entry["id"],
        "recommendation_id": entry["recommendation_id"],
        "decision": entry["decision"],
        "reason": entry["reason"],
        "source": human["source"],
        "actor": human["actor"],
        "decided_at": human[timestamp_field],
        "recommendation_evidence_digest": entry["expected_evidence_digest"],
        "current_evidence": deepcopy(description["current_evidence"]),
        "current_evidence_digest": description["current_evidence_digest"],
        "from_level": recommendation["from_level"],
        "to_level": recommendation["to_level"],
    }


def _persisted_matches_input(persisted, entry):
    if persisted["id"] != entry["id"] or persisted["decision"] != entry["decision"]:
        return False
    if (
        persisted["recommendation_id"] != entry["recommendation_id"]
        or persisted["reason"] != entry["reason"]
        or persisted["recommendation_evidence_digest"]
        != entry["expected_evidence_digest"]
    ):
        return False
    human = entry["approval" if entry["decision"] == "approve" else "rejection"]
    timestamp_field = "approved_at" if entry["decision"] == "approve" else "rejected_at"
    if (
        persisted["source"] != human["source"]
        or persisted["actor"] != human["actor"]
        or persisted["decided_at"] != human[timestamp_field]
    ):
        return False
    return (
        entry["decision"] != "approve"
        or persisted["current_evidence_digest"]
        == entry["expected_current_evidence_digest"]
    )


def _blocked(source, reason, *, decision_id=None, recommendation_id=None):
    return "BLOCKED", source, {
        "reason": reason,
        "decision_id": decision_id,
        "recommendation_id": recommendation_id,
        "decisions": [],
        "levels_changed": [],
    }


def evaluate_project(project, decision_document):
    """Apply a fully preflighted explicit promotion decision batch in memory."""
    transaction_guard = mutation_guard_metadata(project)
    if transaction_guard is not None:
        return _blocked(deepcopy(project), transaction_guard["reason"])
    validate_evaluation_decisions(decision_document)
    source, source_evaluations, source_career, source_competencies = _workspace_states(
        project
    )
    if source_evaluations is None:
        return _blocked(source, "workspace_not_initialized")
    evaluations = deepcopy(source_evaluations)
    career = deepcopy(source_career)
    competencies = deepcopy(source_competencies)
    persisted_by_id = {
        item["id"]: item for item in evaluations.get("promotion_decisions", [])
    }
    recommendations = {
        item["id"]: item for item in evaluations.get("promotion_recommendations", [])
    }
    decision_results = []
    level_changes = []

    for entry in decision_document["decisions"]:
        existing = persisted_by_id.get(entry["id"])
        if existing is not None:
            if not _persisted_matches_input(existing, entry):
                return _blocked(
                    source, "evaluation_decision_conflict",
                    decision_id=entry["id"],
                    recommendation_id=entry["recommendation_id"],
                )
            decision_results.append({
                "id": entry["id"], "recommendation_id": entry["recommendation_id"],
                "decision": "NO_CHANGE",
            })
            continue
        recommendation = recommendations.get(entry["recommendation_id"])
        if recommendation is None:
            return _blocked(
                source, "recommendation_not_found", decision_id=entry["id"],
                recommendation_id=entry["recommendation_id"],
            )
        if recommendation["status"] != "pending":
            return _blocked(
                source, "recommendation_not_pending", decision_id=entry["id"],
                recommendation_id=entry["recommendation_id"],
            )
        if recommendation["evidence_digest"] != entry["expected_evidence_digest"]:
            return _blocked(
                source, "recommendation_evidence_changed", decision_id=entry["id"],
                recommendation_id=entry["recommendation_id"],
            )
        description = _describe_pending(recommendation, career, competencies)
        if description["current_evidence"] is None:
            return _blocked(
                source, "recommendation_stale", decision_id=entry["id"],
                recommendation_id=entry["recommendation_id"],
            )
        if entry["decision"] == "approve":
            if (
                description["stale"]
                or description["current_evidence_digest"]
                != entry["expected_current_evidence_digest"]
            ):
                reason = (
                    "recommendation_stale" if description["stale"]
                    else "recommendation_evidence_changed"
                )
                return _blocked(
                    source, reason, decision_id=entry["id"],
                    recommendation_id=entry["recommendation_id"],
                )
            subject = (
                career if recommendation["scope"] == "career"
                else competencies[recommendation["competency_id"]]
            )
            status, promoted, readiness = promote_if_eligible(
                subject, human_approval=True
            )
            if (
                status != "CHANGE"
                or readiness["target"] != recommendation["to_level"]
                or promoted["level"] != recommendation["to_level"]
            ):
                return _blocked(
                    source, "recommendation_stale", decision_id=entry["id"],
                    recommendation_id=entry["recommendation_id"],
                )
            if recommendation["scope"] == "career":
                validate_promotable_career_state(promoted)
                career = promoted
            else:
                validate_promotable_competency_state(
                    recommendation["competency_id"], promoted
                )
                competencies[recommendation["competency_id"]] = promoted
            recommendation["status"] = "approved"
            level_changes.append({
                "scope": recommendation["scope"],
                "competency_id": recommendation["competency_id"],
                "from_level": recommendation["from_level"],
                "to_level": recommendation["to_level"],
            })
            result = "promote"
        else:
            recommendation["status"] = "rejected"
            result = "maintain"
        persisted = _persisted_decision(entry, recommendation, description)
        evaluations.setdefault("promotion_decisions", []).append(persisted)
        evaluations["history"].append({
            "id": entry["id"], "performed_at": persisted["decided_at"],
            "result": result,
        })
        persisted_by_id[entry["id"]] = persisted
        decision_results.append({
            "id": entry["id"], "recommendation_id": entry["recommendation_id"],
            "decision": entry["decision"].upper(),
        })

    validate_evaluation_state(evaluations)
    changed = (
        evaluations != source_evaluations
        or career != source_career
        or competencies != source_competencies
    )
    out = deepcopy(source)
    if changed:
        out["files"][EVALUATIONS_PATH] = evaluations
        if career != source_career:
            out["files"][CAREER_PATH] = career
        if competencies != source_competencies:
            out["files"][COMPETENCIES_PATH] = competencies
    return ("CHANGE" if changed else "NO_CHANGE"), out, {
        "reason": None,
        "decisions": decision_results,
        "levels_changed": level_changes,
        "pending_recommendations": sum(
            item["status"] == "pending"
            for item in evaluations.get("promotion_recommendations", [])
        ),
    }


def refresh_project_recommendations(project):
    """Withdraw or supersede stale pending recommendations without promotion."""
    transaction_guard = mutation_guard_metadata(project)
    if transaction_guard is not None:
        return _blocked(deepcopy(project), transaction_guard["reason"])
    source, source_evaluations, career, competencies = _workspace_states(project)
    if source_evaluations is None:
        return _blocked(source, "workspace_not_initialized")
    evaluations = deepcopy(source_evaluations)
    refreshed = []
    for recommendation in evaluations.get("promotion_recommendations", []):
        if recommendation["status"] != "pending":
            continue
        description = _describe_pending(recommendation, career, competencies)
        reason = description["stale_reason"]
        if reason is None:
            continue
        recommendation["status"] = (
            "superseded" if reason in {"source_level_changed", "target_changed"}
            else "withdrawn"
        )
        refreshed.append({
            "recommendation_id": recommendation["id"],
            "status": recommendation["status"], "reason": reason,
        })
    validate_evaluation_state(evaluations)
    out = deepcopy(source)
    if refreshed:
        out["files"][EVALUATIONS_PATH] = evaluations
    return ("CHANGE" if refreshed else "NO_CHANGE"), out, {
        "reason": None, "refreshed": refreshed, "levels_changed": [],
        "pending_recommendations": sum(
            item["status"] == "pending"
            for item in evaluations.get("promotion_recommendations", [])
        ),
    }
