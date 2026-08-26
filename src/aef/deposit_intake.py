"""Validate aef.deposit.submit/v1 capture envelopes without touching engine state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import jsonschema

from .ingest_intake import InvalidIngestSubmissionError, _validate_event
from .record_document import (
    RECORD_PROTOCOL,
    InvalidRecordSubmissionError,
    validate_record_id,
    validate_record_submission,
)
from .schema_validation import draft202012_validator, load_packaged_schema
from .strict_json import InvalidStrictJSONError, validate_strict_json


DEPOSIT_PROTOCOL = "aef.deposit.submit/v1"
RECORD_SUBMIT_PROTOCOL = "aef.record.submit/v1"


class InvalidDepositSubmissionError(ValueError):
    """Raised when a deposit capture document is outside contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _reject(code: str, message: str) -> None:
    raise InvalidDepositSubmissionError(code, message)


def _validate_deposit_events(events: Any) -> None:
    if not isinstance(events, list) or not events:
        _reject("invalid_deposit_events", "events must contain at least one event.")
    event_ids: set[str] = set()
    for event in events:
        try:
            _validate_event(event, event_ids)
        except InvalidIngestSubmissionError as exc:
            raise InvalidDepositSubmissionError(exc.code, str(exc)) from exc


def _validate_submitted_identifiers(document: dict[str, Any]) -> None:
    try:
        validate_record_id(document.get("record_id"))
    except InvalidRecordSubmissionError as exc:
        raise InvalidDepositSubmissionError(exc.code, str(exc)) from exc
    events = document.get("events")
    if not isinstance(events, list):
        return
    event_ids: set[str] = set()
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
            raise InvalidDepositSubmissionError(exc.code, str(exc)) from exc


def validate_deposit_submission(document: Any) -> dict[str, Any]:
    """Validate an aef.deposit.submit/v1 document without touching the filesystem."""
    if not isinstance(document, dict):
        _reject("invalid_deposit_submission", "The deposit document must be a JSON object.")
    if document.get("protocol") == RECORD_PROTOCOL or "digest" in document:
        _reject(
            "invalid_deposit_submission",
            "Deposit input must be aef.deposit.submit/v1 without a digest.",
        )
    try:
        validate_strict_json(document)
    except InvalidStrictJSONError as exc:
        raise InvalidDepositSubmissionError(
            "invalid_deposit_submission", "The deposit document is not strict JSON."
        ) from exc
    _validate_submitted_identifiers(document)
    try:
        schema = load_packaged_schema("deposit-submission.schema.json")
        draft202012_validator(schema).validate(document)
    except jsonschema.ValidationError as exc:
        raise InvalidDepositSubmissionError(
            "invalid_deposit_submission",
            "The deposit document does not match aef.deposit.submit/v1.",
        ) from exc
    if document.get("protocol") != DEPOSIT_PROTOCOL:
        _reject("invalid_deposit_protocol", "protocol must be aef.deposit.submit/v1.")

    record_view = deepcopy(document)
    record_view["protocol"] = RECORD_SUBMIT_PROTOCOL
    record_view.pop("events", None)
    try:
        validate_record_submission(record_view)
    except InvalidRecordSubmissionError as exc:
        raise InvalidDepositSubmissionError(exc.code, str(exc)) from exc

    _validate_deposit_events(document.get("events"))
    return deepcopy(document)
