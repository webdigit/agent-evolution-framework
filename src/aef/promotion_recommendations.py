from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from .progression import promotion_readiness
from .identifiers import (
    InvalidCompetencyIdentifierError,
    competency_recommendation_subject,
    validate_competency_id,
)


PROMOTION_RECOMMENDATION_STATUSES = {
    "pending", "approved", "rejected", "withdrawn", "superseded",
}
EVIDENCE_FIELDS = (
    "xp", "cases", "trust", "complex_cases", "recent_significant_errors",
)
LEVEL_ORDER = ("L1", "L2", "L3", "L4", "L5")
RECOMMENDATION_FIELDS = {
    "id", "type", "scope", "competency_id", "from_level", "to_level",
    "status", "detected_at", "evidence", "evidence_digest",
}
EVIDENCE_FIELD_SET = set(EVIDENCE_FIELDS)
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
SCHEMA_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
POLICY_FIELDS = {"mode", "every_tasks", "interval_days"}
POLICY_MODES = {"task_count", "interval", "manual", "adaptive"}
HISTORY_FIELDS = {"id", "performed_at", "result"}
HISTORY_RESULTS = {
    "maintain", "promote", "probation", "demote", "adjust_supervision",
}
EVALUATION_SCHEMA_VERSION = "1.0.0"
PROMOTION_DECISION_FIELDS = {
    "id", "recommendation_id", "decision", "reason", "source", "actor",
    "decided_at", "recommendation_evidence_digest", "current_evidence",
    "current_evidence_digest", "from_level", "to_level",
}


class InvalidPromotionRecommendationStateError(ValueError):
    """Raised when promotion recommendations or their source metrics are invalid."""


def empty_evaluations():
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "policy": {"mode": "adaptive", "every_tasks": None, "interval_days": None},
        "history": [],
        "promotion_recommendations": [],
    }


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _evidence_snapshot(state):
    return {field: deepcopy(state.get(field)) for field in EVIDENCE_FIELDS}


def _evidence_digest(evidence):
    _validate_metrics(evidence)
    payload = json.dumps(
        evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _valid_rfc3339(value):
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        parsed = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(parsed).tzinfo is not None
    except ValueError:
        return False


def _validate_request_parameters(scope, competency_id, detected_at):
    if scope == "career":
        if competency_id is not None:
            raise InvalidPromotionRecommendationStateError(
                "invalid career recommendation scope"
            )
    elif scope == "competency":
        try:
            validate_competency_id(competency_id)
        except InvalidCompetencyIdentifierError as exc:
            raise InvalidPromotionRecommendationStateError(
                "invalid competency recommendation scope; explicit migration required"
            ) from exc
    else:
        raise InvalidPromotionRecommendationStateError("invalid recommendation scope")
    if detected_at is not None and not _valid_rfc3339(detected_at):
        raise InvalidPromotionRecommendationStateError("invalid recommendation timestamp")


def _validate_metrics(metrics):
    if not isinstance(metrics, dict) or set(metrics) != EVIDENCE_FIELD_SET:
        raise InvalidPromotionRecommendationStateError("invalid promotion evidence")
    xp = metrics["xp"]
    if not _is_number(xp) or xp < 0:
        raise InvalidPromotionRecommendationStateError("invalid promotion XP")
    for field in ("cases", "complex_cases", "recent_significant_errors"):
        value = metrics[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidPromotionRecommendationStateError(f"invalid promotion {field}")
    trust = metrics["trust"]
    if trust is not None and (not _is_number(trust) or not 0 <= trust <= 1):
        raise InvalidPromotionRecommendationStateError("invalid promotion Trust")


def validate_progression_snapshot(state):
    """Validate every source field used by promotion readiness and evidence."""
    if not isinstance(state, dict) or state.get("level") not in LEVEL_ORDER:
        raise InvalidPromotionRecommendationStateError("invalid progression level")
    if not EVIDENCE_FIELD_SET.issubset(state):
        raise InvalidPromotionRecommendationStateError("missing progression metrics")
    _validate_metrics({field: state[field] for field in EVIDENCE_FIELDS})
    return state


def _validate_recommendation(entry):
    if not isinstance(entry, dict) or set(entry) != RECOMMENDATION_FIELDS:
        raise InvalidPromotionRecommendationStateError("invalid promotion recommendation")
    recommendation_id = entry["id"]
    if not isinstance(recommendation_id, str) or not recommendation_id.strip():
        raise InvalidPromotionRecommendationStateError("invalid recommendation id")
    if entry["type"] != "promotion":
        raise InvalidPromotionRecommendationStateError("invalid recommendation type")
    scope = entry["scope"]
    competency_id = entry["competency_id"]
    if scope == "career":
        if competency_id is not None:
            raise InvalidPromotionRecommendationStateError("invalid career recommendation scope")
        subject = "global"
    elif scope == "competency":
        try:
            validate_competency_id(competency_id)
        except InvalidCompetencyIdentifierError as exc:
            raise InvalidPromotionRecommendationStateError(
                "invalid competency recommendation scope; explicit migration required"
            ) from exc
        subject = competency_recommendation_subject(competency_id)
    else:
        raise InvalidPromotionRecommendationStateError("invalid recommendation scope")

    from_level = entry["from_level"]
    to_level = entry["to_level"]
    if from_level not in LEVEL_ORDER or to_level not in LEVEL_ORDER:
        raise InvalidPromotionRecommendationStateError("invalid recommendation level")
    index = LEVEL_ORDER.index(from_level)
    if index == len(LEVEL_ORDER) - 1 or LEVEL_ORDER[index + 1] != to_level:
        raise InvalidPromotionRecommendationStateError("invalid recommendation transition")
    expected_id = f"promotion:{scope}:{subject}:{from_level}:{to_level}"
    generated_id = f"{expected_id}:evidence-{entry['evidence_digest'][7:]}"
    if recommendation_id not in {expected_id, generated_id}:
        raise InvalidPromotionRecommendationStateError("incoherent recommendation id")
    if entry["status"] not in PROMOTION_RECOMMENDATION_STATUSES:
        raise InvalidPromotionRecommendationStateError("invalid recommendation status")
    if not _valid_rfc3339(entry["detected_at"]):
        raise InvalidPromotionRecommendationStateError("invalid recommendation timestamp")
    _validate_metrics(entry["evidence"])
    digest = entry["evidence_digest"]
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise InvalidPromotionRecommendationStateError("invalid evidence digest")
    if digest != _evidence_digest(entry["evidence"]):
        raise InvalidPromotionRecommendationStateError("incoherent evidence digest")


def _validate_policy(policy):
    if not isinstance(policy, dict) or "mode" not in policy:
        raise InvalidPromotionRecommendationStateError("invalid evaluation policy")
    if not set(policy).issubset(POLICY_FIELDS) or policy["mode"] not in POLICY_MODES:
        raise InvalidPromotionRecommendationStateError("invalid evaluation policy")
    for field in ("every_tasks", "interval_days"):
        value = policy.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise InvalidPromotionRecommendationStateError(
                f"invalid evaluation policy {field}"
            )


def _validate_history(history):
    if not isinstance(history, list):
        raise InvalidPromotionRecommendationStateError("invalid evaluation history")
    seen_ids = set()
    for entry in history:
        if not isinstance(entry, dict) or set(entry) != HISTORY_FIELDS:
            raise InvalidPromotionRecommendationStateError("invalid evaluation history entry")
        entry_id = entry["id"]
        if not isinstance(entry_id, str) or not entry_id.strip() or entry_id in seen_ids:
            raise InvalidPromotionRecommendationStateError("invalid evaluation history id")
        seen_ids.add(entry_id)
        if not _valid_rfc3339(entry["performed_at"]):
            raise InvalidPromotionRecommendationStateError(
                "invalid evaluation history timestamp"
            )
        if entry["result"] not in HISTORY_RESULTS:
            raise InvalidPromotionRecommendationStateError("invalid evaluation result")


def _validate_promotion_decisions(decisions, recommendations_by_id):
    if not isinstance(decisions, list):
        raise InvalidPromotionRecommendationStateError("invalid promotion decisions")
    seen_ids = set()
    for entry in decisions:
        if not isinstance(entry, dict) or set(entry) != PROMOTION_DECISION_FIELDS:
            raise InvalidPromotionRecommendationStateError("invalid promotion decision")
        if (
            not isinstance(entry["id"], str)
            or not entry["id"].strip()
            or entry["id"] in seen_ids
        ):
            raise InvalidPromotionRecommendationStateError("invalid promotion decision id")
        seen_ids.add(entry["id"])
        recommendation = recommendations_by_id.get(entry["recommendation_id"])
        if recommendation is None:
            raise InvalidPromotionRecommendationStateError(
                "unknown promotion decision recommendation"
            )
        if entry["decision"] not in {"approve", "reject"}:
            raise InvalidPromotionRecommendationStateError("invalid promotion decision outcome")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise InvalidPromotionRecommendationStateError("invalid promotion decision reason")
        if entry["source"] != "human":
            raise InvalidPromotionRecommendationStateError("invalid promotion decision source")
        if not isinstance(entry["actor"], str) or not entry["actor"].strip():
            raise InvalidPromotionRecommendationStateError("invalid promotion decision actor")
        if not _valid_rfc3339(entry["decided_at"]):
            raise InvalidPromotionRecommendationStateError("invalid promotion decision timestamp")
        if entry["from_level"] not in LEVEL_ORDER or entry["to_level"] not in LEVEL_ORDER:
            raise InvalidPromotionRecommendationStateError("invalid promotion decision level")
        index = LEVEL_ORDER.index(entry["from_level"])
        if index == len(LEVEL_ORDER) - 1 or LEVEL_ORDER[index + 1] != entry["to_level"]:
            raise InvalidPromotionRecommendationStateError(
                "invalid promotion decision transition"
            )
        _validate_metrics(entry["current_evidence"])
        for field in ("recommendation_evidence_digest", "current_evidence_digest"):
            digest = entry[field]
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                raise InvalidPromotionRecommendationStateError(
                    "invalid promotion decision digest"
                )
        if entry["current_evidence_digest"] != _evidence_digest(
            entry["current_evidence"]
        ):
            raise InvalidPromotionRecommendationStateError(
                "incoherent promotion decision evidence"
            )
        if entry["recommendation_evidence_digest"] != recommendation["evidence_digest"]:
            raise InvalidPromotionRecommendationStateError(
                "incoherent promotion decision recommendation evidence"
            )


def validate_evaluation_state(evaluations):
    """Validate all AEF-owned evaluation state without normalization or mutation."""
    if not isinstance(evaluations, dict):
        raise InvalidPromotionRecommendationStateError("invalid evaluation state")
    if "schema_version" in evaluations:
        version = evaluations["schema_version"]
        if (
            not isinstance(version, str)
            or not SCHEMA_VERSION_PATTERN.fullmatch(version)
            or version != EVALUATION_SCHEMA_VERSION
        ):
            raise InvalidPromotionRecommendationStateError(
                "invalid evaluation schema version"
            )
    if "policy" not in evaluations or "history" not in evaluations:
        raise InvalidPromotionRecommendationStateError("incomplete evaluation state")
    _validate_policy(evaluations["policy"])
    _validate_history(evaluations["history"])
    collection = evaluations.get("promotion_recommendations", [])
    if not isinstance(collection, list):
        raise InvalidPromotionRecommendationStateError("invalid recommendation collection")
    seen_ids = set()
    recommendations_by_id = {}
    for entry in collection:
        _validate_recommendation(entry)
        if entry["id"] in seen_ids:
            raise InvalidPromotionRecommendationStateError("duplicate recommendation id")
        seen_ids.add(entry["id"])
        recommendations_by_id[entry["id"]] = entry
    if "promotion_decisions" in evaluations:
        _validate_promotion_decisions(
            evaluations["promotion_decisions"], recommendations_by_id
        )
    return evaluations


def validate_promotion_recommendation_state(evaluations):
    """Backward-compatible name for complete evaluation-state validation."""
    return validate_evaluation_state(evaluations)


def ensure_pending_promotion(
    evaluations, state, *, scope, competency_id=None, detected_at=None
):
    """Create the first pending recommendation for an eligible transition.

    The evidence snapshot is immutable after first detection. A future EVALUATE
    operation must recalculate readiness before applying any promotion.
    """
    validate_promotion_recommendation_state(evaluations)
    validate_progression_snapshot(state)
    _validate_request_parameters(scope, competency_id, detected_at)
    readiness = promotion_readiness(state)
    if not readiness["eligible"]:
        return "NO_CHANGE", deepcopy(evaluations), None, False
    if detected_at is None:
        raise InvalidPromotionRecommendationStateError("missing recommendation timestamp")
    from_level = state["level"]
    to_level = readiness["target"]
    subject = (
        "global" if scope == "career"
        else competency_recommendation_subject(competency_id)
    )
    base_id = f"promotion:{scope}:{subject}:{from_level}:{to_level}"
    collection = evaluations.get("promotion_recommendations", [])
    evidence = _evidence_snapshot(state)
    evidence_digest = _evidence_digest(evidence)
    same_transition = [
        item for item in collection
        if item["scope"] == scope
        and item["competency_id"] == competency_id
        and item["from_level"] == from_level
        and item["to_level"] == to_level
    ]
    same_evidence = next(
        (item for item in same_transition if item["evidence_digest"] == evidence_digest),
        None,
    )
    if same_evidence is not None:
        return "NO_CHANGE", deepcopy(evaluations), same_evidence["id"], False
    active = next(
        (item for item in same_transition if item["status"] in {"pending", "approved"}),
        None,
    )
    if active is not None:
        return "NO_CHANGE", deepcopy(evaluations), active["id"], False
    if not same_transition:
        recommendation_id = base_id
    else:
        recommendation_id = f"{base_id}:evidence-{evidence_digest[7:]}"
        existing = next(
            (item for item in same_transition if item["id"] == recommendation_id), None
        )
        if existing is not None:
            return "NO_CHANGE", deepcopy(evaluations), recommendation_id, False

    out = deepcopy(evaluations)
    if "promotion_recommendations" not in out:
        out["promotion_recommendations"] = []
    out["promotion_recommendations"].append({
        "id": recommendation_id,
        "type": "promotion",
        "scope": scope,
        "competency_id": competency_id,
        "from_level": from_level,
        "to_level": to_level,
        "status": "pending",
        "detected_at": detected_at,
        "evidence": evidence,
        "evidence_digest": evidence_digest,
    })
    out["promotion_recommendations"] = sorted(
        out["promotion_recommendations"], key=lambda item: item["id"]
    )
    validate_promotion_recommendation_state(out)
    return "CHANGE", out, recommendation_id, True


def recommendation_metadata(evaluations, new_recommendations):
    validate_promotion_recommendation_state(evaluations)
    pending = any(
        item.get("status") == "pending"
        for item in evaluations.get("promotion_recommendations", [])
    )
    return {
        "review_required": pending,
        "new_recommendations": list(new_recommendations),
    }
