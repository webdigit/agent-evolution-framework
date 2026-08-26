from __future__ import annotations

from copy import deepcopy
from typing import Any

import jsonschema

from .knowledge import union_evidence_ids
from .record_document import InvalidRecordSubmissionError, validate_record_id
from .schema_validation import draft202012_validator, load_packaged_schema
from .strict_json import InvalidStrictJSONError, validate_strict_json


INGEST_PROTOCOL = "aef.ingest.submit/v1"
EVENT_KINDS = frozenset({
    "help_request", "human_correction", "rule_mismatch", "success",
})
SOURCE_RECORDS_KEY = "source_records"


class InvalidIngestSubmissionError(ValueError):
    """Raised when an ingest intake document is outside contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class IngestBlockedError(Exception):
    """Raised when cited records cannot be bound without writing."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _reject(code: str, message: str) -> None:
    raise InvalidIngestSubmissionError(code, message)


def _validate_submitted_identifiers(document: dict[str, Any]) -> None:
    citations = document.get("records")
    if not isinstance(citations, list):
        return
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        if isinstance(citation.get("record_id"), str):
            try:
                validate_record_id(citation.get("record_id"))
            except InvalidRecordSubmissionError as exc:
                raise InvalidIngestSubmissionError(exc.code, str(exc)) from exc
        events = citation.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                event_id = event.get("id")
                if isinstance(event_id, str):
                    validate_record_id(event_id)
                pattern_key = event.get("pattern_key")
                if isinstance(pattern_key, str):
                    validate_record_id(pattern_key)
                competency = event.get("competency")
                if isinstance(competency, str):
                    validate_record_id(competency)
            except InvalidRecordSubmissionError as exc:
                raise InvalidIngestSubmissionError(exc.code, str(exc)) from exc


def validate_ingest_submission(document: Any) -> dict[str, Any]:
    """Validate an aef.ingest.submit/v1 document without touching the filesystem."""
    if not isinstance(document, dict):
        _reject("invalid_ingest_submission", "The ingest document must be a JSON object.")
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidIngestSubmissionError(
            "invalid_ingest_submission", "The ingest document is not strict JSON."
        ) from exc
    _validate_submitted_identifiers(document)
    try:
        schema = load_packaged_schema("ingest-submission.schema.json")
        draft202012_validator(schema).validate(document)
    except jsonschema.ValidationError as exc:
        raise InvalidIngestSubmissionError(
            "invalid_ingest_submission", "The ingest document does not match aef.ingest.submit/v1."
        ) from exc
    if document.get("protocol") != INGEST_PROTOCOL:
        _reject("invalid_ingest_protocol", "protocol must be aef.ingest.submit/v1.")

    record_ids: set[str] = set()
    event_ids: set[str] = set()
    citations = document.get("records")
    if not isinstance(citations, list) or not citations:
        _reject("invalid_ingest_submission", "records must contain at least one citation.")
    for citation in citations:
        if not isinstance(citation, dict):
            _reject("invalid_ingest_citation", "each records entry must be an object.")
        try:
            record_id = validate_record_id(citation.get("record_id"))
        except InvalidRecordSubmissionError as exc:
            raise InvalidIngestSubmissionError(exc.code, str(exc)) from exc
        if record_id in record_ids:
            _reject("duplicate_record_id", "each record_id may appear only once in the intake.")
        record_ids.add(record_id)
        events = citation.get("events")
        if not isinstance(events, list) or not events:
            _reject("invalid_ingest_events", "each citation must declare at least one event.")
        for event in events:
            _validate_event(event, event_ids)
    return deepcopy(document)


def _validate_event(event: Any, event_ids: set[str]) -> None:
    if not isinstance(event, dict):
        _reject("invalid_ingest_event", "each event must be an object.")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        _reject("invalid_event_id", "each event requires a non-empty id.")
    try:
        event_id = validate_record_id(event_id)
    except InvalidRecordSubmissionError as exc:
        raise InvalidIngestSubmissionError(exc.code, str(exc)) from exc
    if event_id in event_ids:
        _reject("duplicate_event_id", "each event id may appear only once in the intake.")
    event_ids.add(event_id)

    kind = event.get("kind")
    novel = event.get("novel")
    if kind is not None and kind not in EVENT_KINDS:
        _reject("invalid_event_kind", "kind must be a detect_learning_signals kind.")
    if novel is True:
        pass
    elif kind in EVENT_KINDS:
        pass
    else:
        _reject(
            "invalid_ingest_event",
            "each event must declare novel=true or a recognized kind.",
        )
    if kind == "rule_mismatch" and not (
        isinstance(event.get("rule_id"), str) and event["rule_id"].strip()
    ):
        _reject("missing_rule_id", "rule_mismatch events require rule_id.")
    if kind == "success" and "explained" not in event:
        _reject("missing_explained", "success events require explained.")
    pattern_key = event.get("pattern_key")
    if pattern_key is not None:
        try:
            validate_record_id(pattern_key)
        except InvalidRecordSubmissionError as exc:
            raise InvalidIngestSubmissionError(exc.code, str(exc)) from exc
    competency = event.get("competency")
    if competency is not None:
        try:
            validate_record_id(competency)
        except InvalidRecordSubmissionError as exc:
            raise InvalidIngestSubmissionError(exc.code, str(exc)) from exc


def flatten_ingest_events(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return declared events in citation order, without inventing fields."""
    intake = validate_ingest_submission(document)
    events: list[dict[str, Any]] = []
    for citation in intake["records"]:
        for event in citation["events"]:
            events.append(deepcopy(event))
    return events


def event_citations(document: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Map event id → {record_id, digest} from a validated intake."""
    intake = validate_ingest_submission(document)
    mapping: dict[str, dict[str, str]] = {}
    for citation in intake["records"]:
        source = {
            "record_id": citation["record_id"],
            "digest": citation["digest"],
        }
        for event in citation["events"]:
            mapping[event["id"]] = source
    return mapping


def bind_ingest_citations(
    document: dict[str, Any],
    persisted_records: dict[str, Any],
) -> dict[str, Any]:
    """Bind citations to in-memory persisted records. No filesystem I/O."""
    intake = validate_ingest_submission(document)
    if not isinstance(persisted_records, dict):
        raise IngestBlockedError(
            "record_missing",
            "cited records are not available.",
        )
    for citation in intake["records"]:
        record_id = citation["record_id"]
        stored = persisted_records.get(record_id)
        if not isinstance(stored, dict):
            raise IngestBlockedError(
                "record_missing",
                "a cited record is not persisted.",
                {"record_id": record_id},
            )
        digest = stored.get("digest")
        if digest != citation["digest"]:
            raise IngestBlockedError(
                "record_digest_mismatch",
                "the cited digest does not match the persisted record.",
                {"record_id": record_id},
            )
    return intake


def provenance_for_item(
    item: dict[str, Any],
    citations: dict[str, dict[str, str]],
    items_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Resolve source_records for one derived knowledge item."""
    sources: dict[tuple[str, str], dict[str, str]] = {}
    evidence_ids = item.get("evidence_ids") or []
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    for evidence_id in evidence_ids:
        if evidence_id in citations:
            source = citations[evidence_id]
            sources[(source["record_id"], source["digest"])] = source
            continue
        if items_by_id and evidence_id in items_by_id:
            for nested in provenance_for_item(
                items_by_id[evidence_id], citations, items_by_id
            ):
                sources[(nested["record_id"], nested["digest"])] = nested
    if not sources:
        event_id = item.get("id")
        if isinstance(event_id, str) and event_id in citations:
            source = citations[event_id]
            sources[(source["record_id"], source["digest"])] = source
    return [
        {"record_id": record_id, "digest": digest}
        for record_id, digest in sorted(sources)
    ]


def _unique_sources(items: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for source in items:
        if not isinstance(source, dict):
            continue
        record_id = source.get("record_id")
        digest = source.get("digest")
        if isinstance(record_id, str) and isinstance(digest, str):
            unique[(record_id, digest)] = {"record_id": record_id, "digest": digest}
    return [
        {"record_id": record_id, "digest": digest}
        for record_id, digest in sorted(unique)
    ]


def attach_source_records(
    state: dict[str, Any],
    citations: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Copy state and attach source_records to ingest-derived items."""
    out = deepcopy(state)
    signals = []
    for item in out.get("signals") or []:
        if not isinstance(item, dict):
            signals.append(item)
            continue
        next_item = deepcopy(item)
        sources = provenance_for_item(next_item, citations)
        if sources:
            next_item[SOURCE_RECORDS_KEY] = sources
        signals.append(next_item)
    out["signals"] = signals
    signals_by_id = {
        item["id"]: item for item in signals
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    observations = []
    for item in out.get("observations") or []:
        if not isinstance(item, dict):
            observations.append(item)
            continue
        next_item = deepcopy(item)
        oid = next_item.get("id")
        if isinstance(oid, str) and oid.startswith("observation:"):
            parent = signals_by_id.get(oid.removeprefix("observation:"))
            if parent and parent.get(SOURCE_RECORDS_KEY):
                next_item[SOURCE_RECORDS_KEY] = deepcopy(parent[SOURCE_RECORDS_KEY])
        observations.append(next_item)
    out["observations"] = observations
    observations_by_id = {
        item["id"]: item for item in observations
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    hypotheses = []
    for item in out.get("hypotheses") or []:
        if not isinstance(item, dict):
            hypotheses.append(item)
            continue
        next_item = deepcopy(item)
        inherited: list[dict[str, str]] = []
        for evidence_id in next_item.get("evidence_ids") or []:
            observation = observations_by_id.get(evidence_id)
            if observation:
                inherited.extend(observation.get(SOURCE_RECORDS_KEY) or [])
        sources = _unique_sources(inherited)
        if sources:
            next_item[SOURCE_RECORDS_KEY] = sources
        hypotheses.append(next_item)
    out["hypotheses"] = hypotheses
    return out


def merge_existing_source_records(
    previous: dict[str, Any],
    next_state: dict[str, Any],
) -> dict[str, Any]:
    """Preserve provenance already stored on matching item ids."""
    out = deepcopy(next_state)
    for collection in ("signals", "observations", "hypotheses"):
        prior = {
            item["id"]: item
            for item in previous.get(collection) or []
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        updated = []
        for item in out.get(collection) or []:
            if not isinstance(item, dict):
                updated.append(item)
                continue
            next_item = deepcopy(item)
            old = prior.get(next_item.get("id"))
            if old:
                merged = _unique_sources(
                    (old.get(SOURCE_RECORDS_KEY) or [])
                    + (next_item.get(SOURCE_RECORDS_KEY) or [])
                )
                if merged:
                    next_item[SOURCE_RECORDS_KEY] = merged
                if "evidence_ids" in old or "evidence_ids" in next_item:
                    next_item["evidence_ids"] = union_evidence_ids(
                        old.get("evidence_ids"),
                        next_item.get("evidence_ids"),
                    )
                if collection == "hypotheses" and old:
                    if "confirmations" in old:
                        next_item["confirmations"] = old["confirmations"]
                    if "explicit_human_validation" in old:
                        next_item["explicit_human_validation"] = old[
                            "explicit_human_validation"
                        ]
                    prior_confirmations = old.get("confirmation_source_records") or []
                    next_confirmations = next_item.get("confirmation_source_records") or []
                    if prior_confirmations or next_confirmations:
                        next_item["confirmation_source_records"] = _unique_sources(
                            list(prior_confirmations) + list(next_confirmations)
                        )
            updated.append(next_item)
        out[collection] = updated
    return out
