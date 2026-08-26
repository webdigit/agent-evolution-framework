"""Learning validation contracts — validation without filesystem I/O."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

import jsonschema

from .learning_lifecycle import confirm_hypothesis
from .record_document import InvalidRecordSubmissionError, validate_record_id
from .schema_validation import draft202012_validator, load_packaged_schema
from .strict_json import InvalidStrictJSONError, validate_strict_json


VALIDATION_PROTOCOL = "aef.learning.validate.submit/v1"
HYPOTHESIS_PREFIX = "hypothesis:"


class InvalidLearningValidationError(ValueError):
    """Raised when a learning validation document is outside contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class LearningValidationBlockedError(Exception):
    """Raised when a valid validation cannot proceed without writing."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _reject(code: str, message: str) -> None:
    raise InvalidLearningValidationError(code, message)


def _validate_rfc3339(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _reject("invalid_human_decision", "decided_at must be valid RFC 3339.")
    try:
        if "T" not in value:
            raise ValueError
        parsed = value[:-1] + "+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(parsed)
        if timestamp.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise InvalidLearningValidationError(
            "invalid_human_decision", "decided_at must be valid RFC 3339."
        ) from exc
    if timestamp.year > 2100:
        _reject("invalid_human_decision", "decided_at is outside the accepted time bound.")
    return value


def validate_cited_hypothesis_id(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(HYPOTHESIS_PREFIX):
        _reject(
            "invalid_hypothesis_id",
            "each hypothesis must be a derived hypothesis id (hypothesis:…).",
        )
    suffix = value.removeprefix(HYPOTHESIS_PREFIX)
    if not suffix:
        _reject("invalid_hypothesis_id", "hypothesis id suffix must be non-empty.")
    try:
        validate_record_id(suffix)
    except InvalidRecordSubmissionError as exc:
        raise InvalidLearningValidationError(exc.code, str(exc)) from exc
    return value


def validate_learning_validation(document: Any) -> dict[str, Any]:
    """Validate an aef.learning.validate.submit/v1 document without I/O."""
    if not isinstance(document, dict):
        _reject("invalid_validation", "The validation document must be a JSON object.")
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidLearningValidationError(
            "invalid_validation", "The validation document is not strict JSON."
        ) from exc
    try:
        schema = load_packaged_schema("learning-validation-submission.schema.json")
        draft202012_validator(schema).validate(document)
    except jsonschema.ValidationError as exc:
        raise InvalidLearningValidationError(
            "invalid_validation",
            "The validation document does not match aef.learning.validate.submit/v1.",
        ) from exc
    if document.get("protocol") != VALIDATION_PROTOCOL:
        _reject("invalid_validation_protocol", "protocol must be aef.learning.validate.submit/v1.")

    decision = document.get("decision")
    if not isinstance(decision, dict):
        _reject("invalid_human_decision", "decision must be a human approval object.")
    if decision.get("source") != "human":
        _reject("invalid_human_decision", "decision.source must be human.")
    if decision.get("approved") is not True:
        _reject("invalid_human_decision", "decision.approved must be true.")
    actor = decision.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        _reject("invalid_human_decision", "decision.actor must be a non-empty string.")
    _validate_rfc3339(decision.get("decided_at"))

    hypothesis_ids: list[str] = []
    seen_hypotheses: set[str] = set()
    for raw_id in document.get("hypotheses") or []:
        hypothesis_id = validate_cited_hypothesis_id(raw_id)
        if hypothesis_id in seen_hypotheses:
            _reject("duplicate_hypothesis_id", "each hypothesis id may appear only once.")
        seen_hypotheses.add(hypothesis_id)
        hypothesis_ids.append(hypothesis_id)

    if not hypothesis_ids:
        _reject(
            "invalid_validation_targets",
            "at least one hypothesis id must be cited.",
        )

    record_ids: set[str] = set()
    citations = document.get("records") or []
    if citations and not isinstance(citations, list):
        _reject("invalid_validation_records", "records must be an array when present.")
    for citation in citations:
        if not isinstance(citation, dict):
            _reject("invalid_validation_citation", "each records entry must be an object.")
        try:
            record_id = validate_record_id(citation.get("record_id"))
        except InvalidRecordSubmissionError as exc:
            raise InvalidLearningValidationError(exc.code, str(exc)) from exc
        if record_id in record_ids:
            _reject("duplicate_record_id", "each record_id may appear only once.")
        record_ids.add(record_id)
        digest = citation.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            _reject("invalid_record_digest", "each citation requires a sha256 digest.")

    out = deepcopy(document)
    out["hypotheses"] = hypothesis_ids
    return out


def bind_validation_records(
    document: dict[str, Any],
    persisted_records: dict[str, Any],
) -> dict[str, Any]:
    """Bind optional citations to in-memory persisted records."""
    validation = validate_learning_validation(document)
    citations = validation.get("records") or []
    if not citations:
        return validation
    if not isinstance(persisted_records, dict):
        raise LearningValidationBlockedError(
            "record_missing",
            "cited records are not available.",
        )
    for citation in citations:
        record_id = citation["record_id"]
        stored = persisted_records.get(record_id)
        if not isinstance(stored, dict):
            raise LearningValidationBlockedError(
                "record_missing",
                "a cited record is not persisted.",
                {"record_id": record_id},
            )
        if stored.get("digest") != citation["digest"]:
            raise LearningValidationBlockedError(
                "record_digest_mismatch",
                "the cited digest does not match the persisted record.",
                {"record_id": record_id},
            )
    return validation


def _hypothesis_index(knowledge: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in knowledge.get("hypotheses") or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _rule_promoted(hypothesis_id: str, rules: list[Any]) -> bool:
    expected_rule_id = f"rule:{hypothesis_id.removeprefix(HYPOTHESIS_PREFIX)}"
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("id") == expected_rule_id:
            return True
        if rule.get("derived_from") == hypothesis_id:
            return True
    return False


def resolve_validation_outcome(
    validation: dict[str, Any],
    knowledge: dict[str, Any],
) -> tuple[str, dict[str, Any], list[str]]:
    """Apply explicit human validation to cited candidate hypotheses."""
    next_knowledge = deepcopy(knowledge)
    validated: list[str] = []
    changed = False

    hypotheses = deepcopy(next_knowledge.get("hypotheses") or [])
    rules = next_knowledge.get("rules") or []
    by_id = _hypothesis_index(next_knowledge)

    for hypothesis_id in validation["hypotheses"]:
        hypothesis = by_id.get(hypothesis_id)
        if hypothesis is None:
            _reject(
                "hypothesis_not_found",
                f"hypothesis {hypothesis_id} is not present in knowledge.",
            )
        if hypothesis.get("status") != "candidate":
            _reject(
                "hypothesis_not_candidate",
                f"hypothesis {hypothesis_id} is not a candidate hypothesis.",
            )
        if _rule_promoted(hypothesis_id, rules):
            if hypothesis.get("explicit_human_validation"):
                continue
            _reject(
                "hypothesis_already_promoted",
                f"hypothesis {hypothesis_id} is already promoted to a rule.",
            )
        status, hypotheses = confirm_hypothesis(
            hypotheses,
            hypothesis_id,
            explicit_human_validation=True,
        )
        if status == "CHANGE":
            changed = True
            validated.append(hypothesis_id)

    next_knowledge["hypotheses"] = hypotheses
    return ("CHANGE" if changed else "NO_CHANGE"), next_knowledge, validated
