from copy import deepcopy


ROLE_DECISION_ID = "decision.role.primary.v1"
SUPPORTED_DECISION_STATUSES = frozenset({"resolved"})


class InvalidDecisionsDocumentError(ValueError):
    """Raised when persisted decisions do not match the canonical engine model."""


def validate_decisions_document(document):
    """Validate persisted decisions without mutating them.

    V1 has exactly one persisted status: ``resolved``. Its producer writes and
    its consumers expect ``id``, ``status``, ``value``, and ``source``.
    """
    if not isinstance(document, dict) or not isinstance(document.get("decisions"), list):
        raise InvalidDecisionsDocumentError("invalid decisions root")

    seen_ids = set()
    for decision in document["decisions"]:
        if not isinstance(decision, dict):
            raise InvalidDecisionsDocumentError("invalid decision entry")
        decision_id = decision.get("id")
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise InvalidDecisionsDocumentError("invalid decision id")
        if decision_id in seen_ids:
            raise InvalidDecisionsDocumentError("duplicate decision id")
        seen_ids.add(decision_id)

        if decision.get("status") not in SUPPORTED_DECISION_STATUSES:
            raise InvalidDecisionsDocumentError("invalid decision status")
        if "value" not in decision:
            raise InvalidDecisionsDocumentError("missing resolved decision value")
        source = decision.get("source")
        if not isinstance(source, str) or not source.strip():
            raise InvalidDecisionsDocumentError("invalid resolved decision source")
        if decision_id == ROLE_DECISION_ID:
            role = decision["value"]
            if not isinstance(role, str) or not role.strip():
                raise InvalidDecisionsDocumentError("invalid primary role")

    return document


def resolve_decision(store, decision_id, value, source="human-confirmed"):
    out = deepcopy(store)
    # Reject malformed persisted state before considering an upsert: replacing
    # one bad entry must never silently repair an otherwise invalid document.
    validate_decisions_document(out)
    existing = next((x for x in out["decisions"] if x["id"] == decision_id), None)
    desired = {"id": decision_id, "status": "resolved", "value": value, "source": source}
    if existing == desired:
        validate_decisions_document(out)
        return "NO_CHANGE", out
    if existing is None:
        out["decisions"].append(desired)
    else:
        existing.clear(); existing.update(desired)
    validate_decisions_document(out)
    return "CHANGE", out


def unresolved(decision_store, required_ids):
    resolved = {x["id"] for x in decision_store["decisions"] if x.get("status") == "resolved"}
    return [x for x in required_ids if x not in resolved]
