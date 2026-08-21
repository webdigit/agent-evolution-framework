from __future__ import annotations

import pytest

from aef.ingest_intake import (
    IngestBlockedError,
    InvalidIngestSubmissionError,
    attach_source_records,
    bind_ingest_citations,
    flatten_ingest_events,
    validate_ingest_submission,
)
from aef.learning_engine import ingest_events


def intake(**overrides):
    document = {
        "protocol": "aef.ingest.submit/v1",
        "records": [
            {
                "record_id": "session-alpha",
                "digest": "sha256:" + ("a" * 64),
                "events": [
                    {"id": "E1", "novel": True, "pattern_key": "init-dry-run"},
                ],
            }
        ],
    }
    document.update(overrides)
    return document


def persisted(record_id="session-alpha", digest=None):
    return {
        record_id: {
            "protocol": "aef.record/v1",
            "record_id": record_id,
            "digest": digest or ("sha256:" + ("a" * 64)),
        }
    }


@pytest.mark.parametrize(
    ("document", "code"),
    [
        ({"protocol": "aef.ingest.submit/v1"}, "invalid_ingest_submission"),
        (intake(protocol="aef.record.submit/v1"), "invalid_ingest_submission"),
        (
            intake(records=[{
                "record_id": "session-alpha",
                "digest": "sha256:" + ("a" * 64),
                "events": [{"id": "E1", "novel": True}],
                "extra": True,
            }]),
            "invalid_ingest_submission",
        ),
        (
            {
                "protocol": "aef.ingest.submit/v1",
                "records": [{
                    "record_id": "../escape",
                    "digest": "sha256:" + ("a" * 64),
                    "events": [{"id": "E1", "novel": True}],
                }],
            },
            "invalid_ingest_submission",
        ),
        (
            {
                "protocol": "aef.ingest.submit/v1",
                "records": [{
                    "record_id": "session-alpha",
                    "digest": "sha256:" + ("a" * 64),
                    "events": [{"id": "E1", "kind": "incident"}],
                }],
            },
            "invalid_ingest_submission",
        ),
        (
            {
                "protocol": "aef.ingest.submit/v1",
                "records": [{
                    "record_id": "session-alpha",
                    "digest": "sha256:" + ("a" * 64),
                    "events": [{"id": "E1"}],
                }],
            },
            "invalid_ingest_event",
        ),
        (
            {
                "protocol": "aef.ingest.submit/v1",
                "records": [{
                    "record_id": "session-alpha",
                    "digest": "sha256:" + ("a" * 64),
                    "events": [{"id": "E1", "kind": "rule_mismatch"}],
                }],
            },
            "missing_rule_id",
        ),
        (
            {
                "protocol": "aef.ingest.submit/v1",
                "records": [{
                    "record_id": "session-alpha",
                    "digest": "sha256:" + ("a" * 64),
                    "events": [{"id": "E1", "kind": "success"}],
                }],
            },
            "missing_explained",
        ),
        (
            {
                "protocol": "aef.ingest.submit/v1",
                "records": [{
                    "record_id": "session-alpha",
                    "digest": "not-a-digest",
                    "events": [{"id": "E1", "novel": True}],
                }],
            },
            "invalid_ingest_submission",
        ),
    ],
)
def test_invalid_intake_is_error_without_defaults(document, code):
    with pytest.raises(InvalidIngestSubmissionError) as raised:
        validate_ingest_submission(document)
    assert raised.value.code == code


def test_competency_is_optional_and_multi_events_are_accepted():
    document = {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": "session-alpha",
            "digest": "sha256:" + ("a" * 64),
            "events": [
                {"id": "E1", "novel": True, "pattern_key": "gap"},
                {"id": "E2", "kind": "help_request", "pattern_key": "gap"},
                {"id": "E3", "kind": "success", "explained": False},
            ],
        }],
    }
    validated = validate_ingest_submission(document)
    assert [event["id"] for event in flatten_ingest_events(validated)] == ["E1", "E2", "E3"]
    assert "competency" not in validated["records"][0]["events"][0]


def test_bind_missing_or_mismatched_record_is_blocked():
    document = intake()
    with pytest.raises(IngestBlockedError) as missing:
        bind_ingest_citations(document, {})
    assert missing.value.code == "record_missing"
    with pytest.raises(IngestBlockedError) as mismatched:
        bind_ingest_citations(document, persisted(digest="sha256:" + ("b" * 64)))
    assert mismatched.value.code == "record_digest_mismatch"


def test_same_intake_is_idempotent_and_keeps_engine_ids():
    document = intake()
    events = flatten_ingest_events(document)
    empty = {
        "signals": [], "observations": [], "hypotheses": [],
        "rules": [], "principles": [], "mistakes": [],
    }
    first_status, first = ingest_events(empty, events)
    second_status, second = ingest_events(first, events)
    assert first_status == "CHANGE"
    assert second_status == "NO_CHANGE"
    assert first["signals"][0]["id"] == "signal:novelty:init-dry-run"
    first_with = attach_source_records(first, {
        "E1": {"record_id": "session-alpha", "digest": "sha256:" + ("a" * 64)},
    })
    replay_with = attach_source_records(second, {
        "E1": {"record_id": "session-alpha", "digest": "sha256:" + ("a" * 64)},
    })
    assert first_with == replay_with
    assert first_with["signals"][0]["source_records"] == [{
        "record_id": "session-alpha",
        "digest": "sha256:" + ("a" * 64),
    }]
    assert first_with["observations"][0]["source_records"][0]["record_id"] == "session-alpha"


def test_bind_accepts_matching_fixture_without_io():
    document = intake()
    bound = bind_ingest_citations(document, persisted())
    assert bound["records"][0]["record_id"] == "session-alpha"
