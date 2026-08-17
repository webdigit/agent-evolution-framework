from copy import deepcopy

from .promotion_recommendations import _valid_rfc3339
from .strict_json import InvalidStrictJSONError, validate_strict_json


CONSOLIDATION_PROTOCOL = "aef.consolidate/v1"
CONSOLIDATION_ACTIONS = {"keep", "specialize", "supersede", "retire"}
COMMON_REVIEW_FIELDS = {"id", "rule_id", "action", "reason", "evidence_ids"}
APPROVAL_FIELDS = {"approved", "source", "actor", "approved_at"}
REPLACEMENT_FIELDS = {"id", "type", "status", "pattern_key", "evidence_ids"}


class InvalidConsolidationInputError(ValueError):
    """Raised when an explicit consolidation review document is invalid."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _approval(value):
    if not isinstance(value, dict) or set(value) != APPROVAL_FIELDS:
        raise InvalidConsolidationInputError("invalid consolidation approval")
    if value.get("approved") is not True or value.get("source") != "human":
        raise InvalidConsolidationInputError("explicit human approval is required")
    if not _text(value.get("actor")) or not _valid_rfc3339(value.get("approved_at")):
        raise InvalidConsolidationInputError("invalid consolidation approval provenance")


def validate_consolidation_replacement(value, *, replaced_rule_id, review_evidence_ids):
    if not isinstance(value, dict) or set(value) != REPLACEMENT_FIELDS:
        raise InvalidConsolidationInputError("invalid replacement rule shape")
    evidence_ids = value.get("evidence_ids")
    if (
        not _text(value.get("id"))
        or value["id"] == replaced_rule_id
        or value.get("type") != "rule"
        or value.get("status") != "active"
        or not _text(value.get("pattern_key"))
        or not isinstance(evidence_ids, list)
        or any(not _text(item) for item in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
        or set(evidence_ids) != set(review_evidence_ids)
    ):
        raise InvalidConsolidationInputError("invalid canonical replacement rule")


def validate_consolidation_document(document):
    """Validate and defensively copy the closed V1 review protocol."""
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidConsolidationInputError("consolidation input is not strict JSON") from exc
    if not isinstance(document, dict) or set(document) != {"protocol", "reviews"}:
        raise InvalidConsolidationInputError("invalid consolidation document")
    if document.get("protocol") != CONSOLIDATION_PROTOCOL or not isinstance(
        document.get("reviews"), list
    ):
        raise InvalidConsolidationInputError("invalid consolidation protocol")

    review_ids = set()
    rule_ids = set()
    replacement_ids = set()
    for review in document["reviews"]:
        if not isinstance(review, dict):
            raise InvalidConsolidationInputError("invalid consolidation review")
        action = review.get("action")
        allowed = set(COMMON_REVIEW_FIELDS)
        if action != "keep":
            allowed.add("approval")
        if action == "specialize":
            allowed.add("context")
        elif action == "supersede":
            allowed.add("replacement")
        if set(review) != allowed or action not in CONSOLIDATION_ACTIONS:
            raise InvalidConsolidationInputError("invalid fields for consolidation action")
        review_id = review.get("id")
        rule_id = review.get("rule_id")
        if (
            not _text(review_id) or review_id in review_ids
            or not _text(rule_id) or rule_id in rule_ids
            or not _text(review.get("reason"))
        ):
            raise InvalidConsolidationInputError("invalid or duplicate consolidation identity")
        review_ids.add(review_id)
        rule_ids.add(rule_id)
        evidence = review.get("evidence_ids")
        if (
            not isinstance(evidence, list)
            or any(not _text(item) for item in evidence)
            or len(evidence) != len(set(evidence))
            or (action != "keep" and not evidence)
        ):
            raise InvalidConsolidationInputError("invalid consolidation evidence")
        if action == "keep":
            if "approval" in review:
                raise InvalidConsolidationInputError("keep cannot be approved")
        else:
            _approval(review.get("approval"))
        if action == "specialize":
            if not isinstance(review.get("context"), dict) or not review["context"]:
                raise InvalidConsolidationInputError("invalid specialization context")
        elif action == "supersede":
            validate_consolidation_replacement(
                review.get("replacement"), replaced_rule_id=rule_id,
                review_evidence_ids=evidence,
            )
            replacement_id = review["replacement"]["id"]
            if replacement_id in replacement_ids:
                raise InvalidConsolidationInputError("duplicate replacement identity")
            replacement_ids.add(replacement_id)
    if (
        review_ids & rule_ids
        or review_ids & replacement_ids
        or rule_ids & replacement_ids
    ):
        raise InvalidConsolidationInputError("cross-category consolidation identity collision")
    normalized = deepcopy(document)
    for review in normalized["reviews"]:
        review["evidence_ids"] = sorted(review["evidence_ids"])
        if review["action"] == "supersede":
            review["replacement"]["evidence_ids"] = sorted(
                review["replacement"]["evidence_ids"]
            )
    normalized["reviews"] = sorted(normalized["reviews"], key=lambda item: item["id"])
    return normalized
