from .strict_json import InvalidStrictJSONError, validate_strict_json
from .promotion_recommendations import _valid_rfc3339
from .consolidation import (
    InvalidConsolidationInputError,
    validate_consolidation_replacement,
)


KNOWLEDGE_COLLECTIONS = (
    "signals", "observations", "hypotheses", "rules", "principles", "mistakes"
)
EVIDENCE_COLLECTIONS = (
    "signals", "observations", "hypotheses", "rules", "mistakes"
)
VALID_KNOWLEDGE_STATUSES = {
    "active", "candidate", "specialized", "retired", "superseded"
}


class InvalidKnowledgeStateError(ValueError):
    """Raised when persisted AEF knowledge is structurally invalid."""


def _non_empty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_evidence_ids(value):
    if not isinstance(value, list) or any(not _non_empty_text(item) for item in value):
        raise InvalidKnowledgeStateError("invalid evidence identifiers")
    if len(value) != len(set(value)):
        raise InvalidKnowledgeStateError("duplicate evidence identifiers")


def _validate_lifecycle_event(rule, event_name):
    lifecycle = rule.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise InvalidKnowledgeStateError("invalid rule lifecycle")
    event = lifecycle.get(event_name)
    if not isinstance(event, dict) or not _non_empty_text(event.get("reason")):
        raise InvalidKnowledgeStateError("invalid rule lifecycle event")
    _validate_evidence_ids(event.get("evidence_ids"))
    if "review_id" not in event and set(event) != {"reason", "evidence_ids"}:
        raise InvalidKnowledgeStateError("invalid historical lifecycle event")
    if "review_id" in event:
        legacy_fields = {
            "review_id", "action", "reason", "evidence_ids", "approval"
        }
        if event_name == "specialized":
            legacy_fields.add("context")
        elif event_name == "superseded":
            legacy_fields.add("replacement_id")
        if "rule_id" not in event:
            if set(event) != legacy_fields or event.get("action") != {
                "specialized": "specialize", "superseded": "supersede", "retired": "retire"
            }[event_name]:
                raise InvalidKnowledgeStateError("invalid legacy reviewed lifecycle event")
            approval = event.get("approval")
            if (
                not isinstance(approval, dict)
                or set(approval) != {"approved", "source", "actor", "approved_at"}
                or approval.get("approved") is not True
                or approval.get("source") != "human"
                or not _non_empty_text(approval.get("actor"))
                or not _valid_rfc3339(approval.get("approved_at"))
            ):
                raise InvalidKnowledgeStateError("invalid legacy lifecycle approval")
            if event_name == "specialized" and event.get("context") != rule.get("context"):
                raise InvalidKnowledgeStateError("inconsistent legacy specialization context")
            if event_name == "superseded" and event.get("replacement_id") != rule.get("superseded_by"):
                raise InvalidKnowledgeStateError("inconsistent legacy supersession link")
            return
        expected = {
            "review_id", "rule_id", "action", "reason", "evidence_ids", "approval"
        }
        expected.add("context" if event_name == "specialized" else "replacement") if event_name in {"specialized", "superseded"} else None
        if set(event) != expected or event.get("action") != {
            "specialized": "specialize", "superseded": "supersede", "retired": "retire"
        }[event_name]:
            raise InvalidKnowledgeStateError("invalid reviewed lifecycle event")
        if (
            not _non_empty_text(event.get("review_id"))
            or event.get("rule_id") != rule.get("id")
        ):
            raise InvalidKnowledgeStateError("invalid lifecycle review identity")
        approval = event.get("approval")
        if (
            not isinstance(approval, dict)
            or set(approval) != {"approved", "source", "actor", "approved_at"}
            or approval.get("approved") is not True
            or approval.get("source") != "human"
            or not _non_empty_text(approval.get("actor"))
            or not _valid_rfc3339(approval.get("approved_at"))
        ):
            raise InvalidKnowledgeStateError("invalid lifecycle approval")
        if event_name == "specialized" and event.get("context") != rule.get("context"):
            raise InvalidKnowledgeStateError("inconsistent specialization context")
        if event_name == "superseded":
            replacement = event.get("replacement")
            if (
                not isinstance(replacement, dict)
                or replacement.get("id") != rule.get("superseded_by")
            ):
                raise InvalidKnowledgeStateError("inconsistent supersession link")
            try:
                validate_consolidation_replacement(
                    replacement,
                    replaced_rule_id=rule.get("id"),
                    review_evidence_ids=event["evidence_ids"],
                )
            except InvalidConsolidationInputError as exc:
                raise InvalidKnowledgeStateError("invalid persisted replacement") from exc


def validate_knowledge_state(state):
    """Validate persisted knowledge without normalizing or mutating it."""
    try:
        validate_strict_json(state)
    except InvalidStrictJSONError as exc:
        raise InvalidKnowledgeStateError("knowledge is not strict JSON") from exc
    if not isinstance(state, dict):
        raise InvalidKnowledgeStateError("knowledge root must be an object")
    if set(state) - set(KNOWLEDGE_COLLECTIONS):
        raise InvalidKnowledgeStateError("unknown knowledge property")
    if not {"observations", "hypotheses", "rules"}.issubset(state):
        raise InvalidKnowledgeStateError("missing knowledge collection")
    if "principles" not in state and "mistakes" not in state:
        raise InvalidKnowledgeStateError("missing canonical or legacy knowledge collection")

    for collection_name in KNOWLEDGE_COLLECTIONS:
        if collection_name not in state:
            continue
        records = state[collection_name]
        if not isinstance(records, list):
            raise InvalidKnowledgeStateError("knowledge collection must be a list")
        identifiers = set()
        for record in records:
            if not isinstance(record, dict):
                raise InvalidKnowledgeStateError("knowledge record must be an object")
            identifier = record.get("id")
            if not _non_empty_text(identifier) or identifier in identifiers:
                raise InvalidKnowledgeStateError("invalid or duplicate knowledge identifier")
            identifiers.add(identifier)
            if record.get("status") not in VALID_KNOWLEDGE_STATUSES:
                raise InvalidKnowledgeStateError("invalid knowledge status")
            if "evidence_ids" in record:
                _validate_evidence_ids(record["evidence_ids"])
            if collection_name == "principles":
                if (
                    record.get("type") != "principle"
                    or not _non_empty_text(record.get("derived_from"))
                    or record.get("human_approved") is not True
                ):
                    raise InvalidKnowledgeStateError("invalid principle")
            if collection_name == "rules":
                status = record["status"]
                if status == "specialized":
                    if not isinstance(record.get("context"), dict) or not record["context"]:
                        raise InvalidKnowledgeStateError("invalid specialized rule context")
                    _validate_lifecycle_event(record, "specialized")
                elif status == "superseded":
                    if not _non_empty_text(record.get("superseded_by")):
                        raise InvalidKnowledgeStateError("invalid superseded rule link")
                    _validate_lifecycle_event(record, "superseded")
                elif status == "retired":
                    _validate_lifecycle_event(record, "retired")
    return state
