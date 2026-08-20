from copy import deepcopy

import pytest

from aef.record_document import (
    InvalidPersistedRecordError,
    InvalidRecordSubmissionError,
    build_persisted_record,
    compute_record_digest,
    validate_persisted_record,
    validate_record_id,
    validate_record_submission,
    validate_recorded_at,
)


def submission(**overrides):
    document = {
        "protocol": "aef.record.submit/v1",
        "record_id": "session-alpha",
        "recorded_at": "2026-08-20T13:21:00Z",
        "declared_by": {"kind": "human", "identifier": "operator"},
        "payload": {
            "context": "reviewed a failed dry-run",
            "actions": [{"summary": "inspected the CLI envelope"}],
            "outcomes": [],
            "incidents": [],
            "evidence": [],
        },
    }
    document.update(overrides)
    return document


def test_valid_submission_builds_persisted_record_with_digest():
    persisted = build_persisted_record(submission())

    assert persisted["protocol"] == "aef.record/v1"
    assert persisted["record_id"] == "session-alpha"
    assert persisted["recorded_at"] == "2026-08-20T13:21:00Z"
    assert persisted["digest"].startswith("sha256:")
    assert len(persisted["digest"]) == 71
    assert "external_metrics" not in persisted
    validate_persisted_record(persisted)


def test_digest_is_stable_and_excludes_submission_protocol():
    first = build_persisted_record(submission())
    second = build_persisted_record(submission())
    body = {key: value for key, value in first.items() if key != "digest"}

    assert first["digest"] == second["digest"]
    assert compute_record_digest(body) == first["digest"]
    assert compute_record_digest({**body, "protocol": "aef.record.submit/v1"}) != first["digest"]


def test_digest_changes_when_semantic_bytes_change():
    first = build_persisted_record(submission())
    changed = submission()
    changed["payload"] = deepcopy(changed["payload"])
    changed["payload"]["context"] = "reviewed a failed dry-run "
    second = build_persisted_record(changed)

    assert first["digest"] != second["digest"]


def test_key_order_does_not_change_digest():
    first = build_persisted_record(submission())
    reordered = {
        "payload": submission()["payload"],
        "declared_by": {"identifier": "operator", "kind": "human"},
        "recorded_at": "2026-08-20T13:21:00Z",
        "record_id": "session-alpha",
        "protocol": "aef.record.submit/v1",
    }

    assert build_persisted_record(reordered)["digest"] == first["digest"]


@pytest.mark.parametrize("record_id", [
    "",
    "Session-alpha",
    "session alpha",
    "session/alpha",
    "session\\alpha",
    "session:alpha",
    ".hidden",
    "hidden.",
    " trailing",
    "trailing ",
    "a..b",
    "con",
    "com1",
    "com1.session",
    "lpt9",
    "a" * 129,
])
def test_record_id_is_rejected_without_cleanup(record_id):
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_id(record_id)
    assert raised.value.code == "invalid_record_id"
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_submission(submission(record_id=record_id))
    assert raised.value.code == "invalid_record_id"


@pytest.mark.parametrize("recorded_at", [
    "2026-08-20T13:21:00+00:00",
    "2026-08-20 13:21:00Z",
    "2026-08-20T13:21:00",
    "2026-02-30T13:21:00Z",
    "2026-08-20T13:21:00.Z",
    "",
])
def test_recorded_at_rejects_non_canonical_utc(recorded_at):
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_recorded_at(recorded_at)
    assert raised.value.code == "invalid_recorded_at"


def test_recorded_at_fraction_is_preserved_exactly():
    persisted = build_persisted_record(submission(recorded_at="2026-08-20T13:21:00.123456789Z"))
    assert persisted["recorded_at"] == "2026-08-20T13:21:00.123456789Z"


def test_whitespace_only_context_is_rejected():
    document = submission()
    document["payload"] = deepcopy(document["payload"])
    document["payload"]["context"] = "   "
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_submission(document)
    assert raised.value.code == "invalid_record_submission"


def test_four_empty_collections_are_rejected():
    document = submission()
    document["payload"] = deepcopy(document["payload"])
    document["payload"]["actions"] = []
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_submission(document)
    assert raised.value.code == "invalid_record_submission"


def test_scalar_collection_item_is_rejected():
    document = submission()
    document["payload"] = deepcopy(document["payload"])
    document["payload"]["actions"] = ["inspected"]
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_submission(document)
    assert raised.value.code == "invalid_record_submission"


def test_digest_or_persisted_protocol_is_rejected_as_submission():
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_submission(submission(digest="sha256:" + ("a" * 64)))
    assert raised.value.code == "invalid_record_submission"
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_submission(submission(protocol="aef.record/v1"))
    assert raised.value.code == "invalid_record_submission"


def test_external_metrics_absent_is_not_zero_filled():
    persisted = build_persisted_record(submission())
    assert "external_metrics" not in persisted


def test_external_metrics_empty_or_implicit_zero_is_rejected():
    with pytest.raises(InvalidRecordSubmissionError):
        validate_record_submission(submission(external_metrics={}))
    with pytest.raises(InvalidRecordSubmissionError):
        validate_record_submission(submission(external_metrics=None))
    with pytest.raises(InvalidRecordSubmissionError):
        validate_record_submission(submission(external_metrics={
            "duration": {"value": 0, "unit": "s", "measurement": "measured"},
            "provider": "openai",
        }))


def test_external_metrics_valid_values_are_copied():
    metrics = {
        "duration": {"value": 12.5, "unit": "s", "measurement": "measured"},
        "tokens_in": {"value": 10, "unit": "token", "measurement": "reported"},
        "cost": {"value": 0, "unit": "usd", "measurement": "estimated"},
    }
    persisted = build_persisted_record(submission(external_metrics=metrics))
    assert persisted["external_metrics"] == metrics


def test_persisted_digest_tamper_is_detected():
    persisted = build_persisted_record(submission())
    persisted["digest"] = "sha256:" + ("0" * 64)
    with pytest.raises(InvalidPersistedRecordError) as raised:
        validate_persisted_record(persisted)
    assert raised.value.code == "record-digest-mismatch"
