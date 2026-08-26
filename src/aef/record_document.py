from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import jsonschema

from .filesystem import WINDOWS_RESERVED_NAMES
from .identifiers import colon_message_if_present, SUBMITTED_IDENTIFIER_PATTERN
from .schema_validation import draft202012_validator, load_packaged_schema
from .strict_json import InvalidStrictJSONError, validate_strict_json


SUBMIT_PROTOCOL = "aef.record.submit/v1"
RECORD_PROTOCOL = "aef.record/v1"
DIGEST_PREFIX = "sha256:"
RECORD_ID_PATTERN = SUBMITTED_IDENTIFIER_PATTERN
RECORDED_AT_PATTERN = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z$"
)
METRIC_UNITS = {
    "duration": "s",
    "tokens_in": "token",
    "tokens_out": "token",
    "cost": "usd",
}
MEASUREMENTS = frozenset({"measured", "reported", "estimated"})
PAYLOAD_COLLECTIONS = ("actions", "outcomes", "incidents", "evidence")


class InvalidRecordSubmissionError(ValueError):
    """Raised when a RECORD submission or persist body is outside contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class InvalidPersistedRecordError(ValueError):
    """Raised when a persisted aef.record/v1 document is invalid."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _reject_submission(code: str, message: str) -> None:
    raise InvalidRecordSubmissionError(code, message)


def _reject_persisted(code: str, message: str) -> None:
    raise InvalidPersistedRecordError(code, message)


def validate_record_id(record_id: Any) -> str:
    """Reject a non-canonical filesystem-safe record_id without cleaning it."""
    colon_message = colon_message_if_present(record_id)
    if colon_message is not None:
        _reject_submission("invalid_record_id", colon_message)
    if not isinstance(record_id, str) or not RECORD_ID_PATTERN.fullmatch(record_id):
        _reject_submission("invalid_record_id", "record_id is not a canonical filesystem-safe identifier.")
    if ".." in record_id:
        _reject_submission("invalid_record_id", "record_id is not a canonical filesystem-safe identifier.")
    first_component = record_id.split(".", 1)[0].upper()
    if first_component in WINDOWS_RESERVED_NAMES:
        _reject_submission("invalid_record_id", "record_id is not a canonical filesystem-safe identifier.")
    return record_id


def validate_recorded_at(recorded_at: Any) -> str:
    """Accept only an explicit canonical RFC 3339 UTC string ending with Z."""
    if not isinstance(recorded_at, str) or not RECORDED_AT_PATTERN.fullmatch(recorded_at):
        _reject_submission("invalid_recorded_at", "recorded_at must be canonical RFC 3339 UTC ending with Z.")
    try:
        parsed = datetime.fromisoformat(recorded_at[:-1] + "+00:00")
    except ValueError as exc:
        raise InvalidRecordSubmissionError(
            "invalid_recorded_at", "recorded_at must be canonical RFC 3339 UTC ending with Z."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        _reject_submission("invalid_recorded_at", "recorded_at must be canonical RFC 3339 UTC ending with Z.")
    reconstructed = parsed.strftime("%Y-%m-%dT%H:%M:%S")
    fraction = RECORDED_AT_PATTERN.fullmatch(recorded_at).group(7)
    if fraction:
        reconstructed = f"{reconstructed}.{fraction}Z"
    else:
        reconstructed = reconstructed + "Z"
    if reconstructed != recorded_at:
        _reject_submission("invalid_recorded_at", "recorded_at must be canonical RFC 3339 UTC ending with Z.")
    return recorded_at


def _validate_identifier(identifier: Any) -> None:
    if not isinstance(identifier, str) or not identifier or identifier.isspace():
        _reject_submission("invalid_record_submission", "declared_by.identifier must be a non-empty string.")


def _validate_context(context: Any) -> None:
    if not isinstance(context, str) or not any(not character.isspace() for character in context):
        _reject_submission("invalid_record_submission", "payload.context must contain a non-whitespace character.")


def _validate_collections(payload: dict[str, Any]) -> None:
    nonempty = False
    for name in PAYLOAD_COLLECTIONS:
        items = payload.get(name)
        if not isinstance(items, list):
            _reject_submission("invalid_record_submission", "payload collections must be arrays.")
        for item in items:
            if not isinstance(item, dict) or not item:
                _reject_submission(
                    "invalid_record_submission",
                    "payload collection items must be non-empty JSON objects.",
                )
        if items:
            nonempty = True
    if not nonempty:
        _reject_submission(
            "invalid_record_submission",
            "at least one of actions, outcomes, incidents, or evidence must contain an item.",
        )


def _validate_metric_value(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject_submission("invalid_record_submission", "external_metrics values must be finite numbers.")
    if isinstance(value, float) and not math.isfinite(value):
        _reject_submission("invalid_record_submission", "external_metrics values must be finite numbers.")
    if value < 0:
        _reject_submission("invalid_record_submission", "external_metrics values must be non-negative.")


def _validate_external_metrics(metrics: Any) -> None:
    if not isinstance(metrics, dict) or not metrics:
        _reject_submission("invalid_record_submission", "external_metrics must declare at least one metric.")
    unexpected = set(metrics) - set(METRIC_UNITS)
    if unexpected:
        _reject_submission("invalid_record_submission", "external_metrics contains an unknown metric.")
    for name, unit in METRIC_UNITS.items():
        if name not in metrics:
            continue
        entry = metrics[name]
        if not isinstance(entry, dict) or set(entry) != {"value", "unit", "measurement"}:
            _reject_submission("invalid_record_submission", "each external metric must declare value, unit, and measurement.")
        _validate_metric_value(entry["value"])
        if entry.get("unit") != unit:
            _reject_submission("invalid_record_submission", "external_metrics units must be the contractual units.")
        if entry.get("measurement") not in MEASUREMENTS:
            _reject_submission("invalid_record_submission", "external_metrics measurement is invalid.")


def _validate_schema(document: dict[str, Any], schema_name: str, *, persist: bool) -> None:
    try:
        validate_strict_json(document)
        schema = load_packaged_schema(schema_name)
        draft202012_validator(schema).validate(document)
    except (InvalidStrictJSONError, jsonschema.ValidationError, TypeError, ValueError) as exc:
        if persist:
            raise InvalidPersistedRecordError(
                "invalid-record", "persisted record is not a valid aef.record/v1 document."
            ) from exc
        raise InvalidRecordSubmissionError(
            "invalid_record_submission",
            "the recording document is not a valid aef.record.submit/v1 submission.",
        ) from exc


def _canonical_body(
    record_id: str,
    recorded_at: str,
    declared_by: dict[str, Any],
    payload: dict[str, Any],
    external_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "declared_by": {
            "identifier": declared_by["identifier"],
            "kind": declared_by["kind"],
        },
        "payload": {
            "actions": deepcopy(payload["actions"]),
            "context": payload["context"],
            "evidence": deepcopy(payload["evidence"]),
            "incidents": deepcopy(payload["incidents"]),
            "outcomes": deepcopy(payload["outcomes"]),
        },
        "protocol": RECORD_PROTOCOL,
        "record_id": record_id,
        "recorded_at": recorded_at,
    }
    if external_metrics is not None:
        body["external_metrics"] = deepcopy(external_metrics)
    return body


def compute_record_digest(body: dict[str, Any]) -> str:
    """Hash the persisted body without the digest field."""
    hashed = {key: value for key, value in body.items() if key != "digest"}
    try:
        validate_strict_json(hashed)
        payload = json.dumps(
            hashed, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (InvalidStrictJSONError, TypeError, ValueError) as exc:
        raise InvalidRecordSubmissionError(
            "invalid_record_submission", "record digest input is not strict JSON."
        ) from exc
    digest = DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        _reject_submission("invalid_record_submission", "record digest is malformed.")
    return digest


def _validate_shared_fields(document: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    record_id = validate_record_id(document.get("record_id"))
    recorded_at = validate_recorded_at(document.get("recorded_at"))
    declared_by = document.get("declared_by")
    if not isinstance(declared_by, dict):
        _reject_submission("invalid_record_submission", "declared_by is required.")
    _validate_identifier(declared_by.get("identifier"))
    if declared_by.get("kind") not in {"human", "agent"}:
        _reject_submission("invalid_record_submission", "declared_by.kind must be human or agent.")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        _reject_submission("invalid_record_submission", "payload is required.")
    _validate_context(payload.get("context"))
    _validate_collections(payload)
    metrics = document.get("external_metrics", _MISSING)
    if metrics is _MISSING:
        external_metrics = None
    else:
        _validate_external_metrics(metrics)
        external_metrics = metrics
    return record_id, recorded_at, declared_by, payload, external_metrics


_MISSING = object()


def validate_record_submission(document: Any) -> dict[str, Any]:
    """Validate aef.record.submit/v1. Digest is forbidden."""
    if not isinstance(document, dict):
        _reject_submission("invalid_record_submission", "the recording document must be a JSON object.")
    if document.get("protocol") == RECORD_PROTOCOL or "digest" in document:
        _reject_submission(
            "invalid_record_submission",
            "RECORD input must be aef.record.submit/v1 without a digest.",
        )
    if "record_id" in document:
        validate_record_id(document.get("record_id"))
    if "recorded_at" in document:
        validate_recorded_at(document.get("recorded_at"))
    _validate_schema(document, "record-submission.schema.json", persist=False)
    _validate_shared_fields(document)
    return document


def build_persisted_record(submission: Any) -> dict[str, Any]:
    """Build aef.record/v1 with an AEF-computed digest. No field is normalized."""
    validate_record_submission(submission)
    record_id, recorded_at, declared_by, payload, external_metrics = _validate_shared_fields(submission)
    body = _canonical_body(record_id, recorded_at, declared_by, payload, external_metrics)
    digest = compute_record_digest(body)
    persisted = deepcopy(body)
    persisted["digest"] = digest
    return persisted


def validate_persisted_record(document: Any) -> dict[str, Any]:
    """Validate a persisted aef.record/v1 document and its digest."""
    if not isinstance(document, dict):
        _reject_persisted("invalid-record", "persisted record is not a valid aef.record/v1 document.")
    if document.get("protocol") != RECORD_PROTOCOL:
        _reject_persisted("invalid-record", "persisted record is not a valid aef.record/v1 document.")
    try:
        _validate_schema(document, "record.schema.json", persist=True)
        record_id, recorded_at, declared_by, payload, external_metrics = _validate_shared_fields(document)
    except InvalidRecordSubmissionError as exc:
        raise InvalidPersistedRecordError(
            "invalid-record", "persisted record is not a valid aef.record/v1 document."
        ) from exc
    stored_digest = document.get("digest")
    if not isinstance(stored_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", stored_digest):
        _reject_persisted("record-digest-mismatch", "persisted record digest is missing or malformed.")
    body = _canonical_body(record_id, recorded_at, declared_by, payload, external_metrics)
    expected = compute_record_digest(body)
    if stored_digest != expected:
        _reject_persisted("record-digest-mismatch", "persisted record digest does not match the canonical body.")
    return document


def record_relative_path(record_id: str) -> str:
    validate_record_id(record_id)
    return f".agent/records/{record_id}.json"
