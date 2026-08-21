"""Competency declaration contracts — validation without filesystem I/O."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy
from datetime import datetime
from typing import Any

import jsonschema

from .identifiers import InvalidCompetencyIdentifierError, validate_competency_id
from .record_document import InvalidRecordSubmissionError, validate_record_id
from .schema_validation import draft202012_validator, load_packaged_schema
from .strict_json import InvalidStrictJSONError, validate_strict_json


DECLARATION_PROTOCOL = "aef.competency.declare.submit/v1"
LEDGER_PROTOCOL = "aef.competency-declarations/v1"
LEDGER_PATH = ".agent/state/competency-declarations.json"
COMPETENCIES_PATH = ".agent/state/competencies.json"
DIGEST_PREFIX = "sha256:"


class InvalidCompetencyDeclarationError(ValueError):
    """Raised when a declaration document is outside contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class CompetencyDeclarationBlockedError(Exception):
    """Raised when a valid declaration cannot proceed without writing."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _reject(code: str, message: str) -> None:
    raise InvalidCompetencyDeclarationError(code, message)


def _canonical_json(value: Any) -> str:
    validate_strict_json(value)
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def declaration_digest(document: dict[str, Any]) -> str:
    """Stable digest of the validated declaration document."""
    return DIGEST_PREFIX + hashlib.sha256(
        _canonical_json(document).encode("utf-8")
    ).hexdigest()


def competency_id_collides(candidate: str, existing_ids: list[str]) -> str | None:
    """Return the colliding existing id, if any, without normalizing persisted ids."""
    candidate_fold = candidate.casefold()
    candidate_nfc = unicodedata.normalize("NFC", candidate)
    for existing in existing_ids:
        if existing == candidate:
            continue
        if existing.casefold() == candidate_fold:
            return existing
        if unicodedata.normalize("NFC", existing) == candidate_nfc:
            return existing
    return None


def projected_l1_entry(document: dict[str, Any]) -> dict[str, Any]:
    """Return the only legal initial competency state for a declaration."""
    competency_id = document["competency_id"]
    return {
        "id": competency_id,
        "title": document["title"],
        "level": "L1",
        "xp": 0,
        "cases": 0,
        "trust": None,
        "complex_cases": 0,
        "recent_significant_errors": 0,
        "probation": False,
        "source": "declared",
    }


def birth_fingerprint(entry: dict[str, Any], event: dict[str, Any]) -> str:
    payload = {"entry": entry, "event": event}
    return DIGEST_PREFIX + hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()


def build_declaration_event(document: dict[str, Any]) -> dict[str, Any]:
    digest = declaration_digest(document)
    return {
        "event_id": "competency-declaration:" + digest[len(DIGEST_PREFIX):],
        "competency_id": document["competency_id"],
        "declared_at": document["decision"]["decided_at"],
        "decision": deepcopy(document["decision"]),
        "records": deepcopy(document["records"]),
        "title": document["title"],
        "scope": document["scope"],
        "limits": document["limits"],
        "rationale": document["rationale"],
        "declaration_digest": digest,
    }


def empty_ledger() -> dict[str, Any]:
    return {"protocol": LEDGER_PROTOCOL, "events": []}


def validate_ledger(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        _reject("invalid_declaration_ledger", "The declaration ledger must be a JSON object.")
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidCompetencyDeclarationError(
            "invalid_declaration_ledger", "The declaration ledger is not strict JSON."
        ) from exc
    if document.get("protocol") != LEDGER_PROTOCOL:
        _reject("invalid_declaration_ledger", "protocol must be aef.competency-declarations/v1.")
    events = document.get("events")
    if not isinstance(events, list):
        _reject("invalid_declaration_ledger", "events must be an array.")
    return deepcopy(document)


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
        raise InvalidCompetencyDeclarationError(
            "invalid_human_decision", "decided_at must be valid RFC 3339."
        ) from exc
    return value


def validate_competency_declaration(document: Any) -> dict[str, Any]:
    """Validate an aef.competency.declare.submit/v1 document without I/O."""
    if not isinstance(document, dict):
        _reject("invalid_declaration", "The declaration document must be a JSON object.")
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidCompetencyDeclarationError(
            "invalid_declaration", "The declaration document is not strict JSON."
        ) from exc
    try:
        schema = load_packaged_schema("competency-declaration-submission.schema.json")
        draft202012_validator(schema).validate(document)
    except jsonschema.ValidationError as exc:
        raise InvalidCompetencyDeclarationError(
            "invalid_declaration",
            "The declaration document does not match aef.competency.declare.submit/v1.",
        ) from exc
    if document.get("protocol") != DECLARATION_PROTOCOL:
        _reject("invalid_declaration_protocol", "protocol must be aef.competency.declare.submit/v1.")

    try:
        competency_id = validate_competency_id(document.get("competency_id"))
    except InvalidCompetencyIdentifierError as exc:
        raise InvalidCompetencyDeclarationError(
            "invalid_competency_id", str(exc)
        ) from exc

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

    forbidden_level = document.get("level")
    if forbidden_level is not None and forbidden_level != "L1":
        _reject("invalid_initial_level", "Only L1 may be declared; other levels are rejected.")
    for forbidden in ("xp", "trust", "exploration_authority", "permission"):
        if forbidden in document:
            _reject(
                "invalid_declaration_authority",
                f"{forbidden} is forbidden on a competency declaration.",
            )

    record_ids: set[str] = set()
    citations = document.get("records")
    if not isinstance(citations, list) or not citations:
        _reject("invalid_declaration_records", "records must contain at least one citation.")
    for citation in citations:
        if not isinstance(citation, dict):
            _reject("invalid_declaration_citation", "each records entry must be an object.")
        try:
            record_id = validate_record_id(citation.get("record_id"))
        except InvalidRecordSubmissionError as exc:
            raise InvalidCompetencyDeclarationError(exc.code, str(exc)) from exc
        if record_id in record_ids:
            _reject("duplicate_record_id", "each record_id may appear only once.")
        record_ids.add(record_id)
        digest = citation.get("digest")
        if not isinstance(digest, str) or not digest.startswith(DIGEST_PREFIX):
            _reject("invalid_record_digest", "each citation requires a sha256 digest.")

    out = deepcopy(document)
    out["competency_id"] = competency_id
    return out


def bind_declaration_records(
    document: dict[str, Any],
    persisted_by_id: dict[str, Any],
) -> None:
    """Ensure cited digests match persisted aef.record/v1 documents."""
    for citation in document["records"]:
        record_id = citation["record_id"]
        stored = persisted_by_id.get(record_id)
        if stored is None:
            raise CompetencyDeclarationBlockedError(
                "record_missing",
                "a cited record is not persisted.",
                {"record_id": record_id},
            )
        if not isinstance(stored, dict):
            raise CompetencyDeclarationBlockedError(
                "record_unreadable",
                "a cited record is not a readable aef.record/v1 document.",
                {"record_id": record_id},
            )
        digest = stored.get("digest")
        if digest != citation["digest"]:
            raise CompetencyDeclarationBlockedError(
                "record_digest_mismatch",
                "a cited digest does not match the persisted record.",
                {"record_id": record_id},
            )


def resolve_declaration_outcome(
    document: dict[str, Any],
    competencies: dict[str, Any],
    ledger: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Project L1 + ledger event. Returns status, next competencies, next ledger."""
    competency_id = document["competency_id"]
    if not isinstance(competencies, dict):
        raise CompetencyDeclarationBlockedError(
            "invalid_competency_state",
            "persisted competencies are not a JSON object.",
        )
    # Reject legacy list envelope for declaration writes.
    if "competencies" in competencies and isinstance(competencies.get("competencies"), list):
        raise CompetencyDeclarationBlockedError(
            "invalid_competency_state",
            "legacy competency list envelope cannot receive a declaration.",
        )

    existing_ids = [key for key in competencies.keys() if isinstance(key, str)]
    collision = competency_id_collides(competency_id, existing_ids)
    if collision is not None:
        raise CompetencyDeclarationBlockedError(
            "competency_id_collision",
            "a case or Unicode-equivalent competency_id already exists.",
            {"competency_id": competency_id, "existing_id": collision},
        )

    entry = projected_l1_entry(document)
    event = build_declaration_event(document)
    fingerprint = birth_fingerprint(entry, event)

    existing = competencies.get(competency_id)
    events = list(ledger.get("events") or [])
    matching_event = next(
        (
            item for item in events
            if isinstance(item, dict) and item.get("competency_id") == competency_id
        ),
        None,
    )

    if existing is not None:
        if not isinstance(existing, dict):
            raise CompetencyDeclarationBlockedError(
                "competency_conflict",
                "an existing competency entry is not an object.",
                {"competency_id": competency_id},
            )
        if matching_event is not None:
            existing_fp = birth_fingerprint(existing, matching_event)
            if existing_fp == fingerprint and existing == entry:
                return "NO_CHANGE", deepcopy(competencies), deepcopy(ledger)
        raise CompetencyDeclarationBlockedError(
            "competency_conflict",
            "a competency with this id already exists with a divergent birth.",
            {"competency_id": competency_id},
        )

    if matching_event is not None:
        raise CompetencyDeclarationBlockedError(
            "declaration_ledger_conflict",
            "a declaration event exists without a matching competency entry.",
            {"competency_id": competency_id},
        )

    next_competencies = deepcopy(competencies)
    next_competencies[competency_id] = deepcopy(entry)
    next_ledger = deepcopy(ledger) if ledger else empty_ledger()
    next_ledger.setdefault("protocol", LEDGER_PROTOCOL)
    next_events = list(next_ledger.get("events") or [])
    next_events.append(deepcopy(event))
    next_ledger["events"] = next_events
    return "CHANGE", next_competencies, next_ledger
