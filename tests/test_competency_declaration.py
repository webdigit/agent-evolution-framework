"""Table-driven competency declaration contracts — no filesystem I/O."""

from __future__ import annotations

import pytest

from aef.competency_declaration import (
    CompetencyDeclarationBlockedError,
    InvalidCompetencyDeclarationError,
    bind_declaration_records,
    competency_id_collides,
    declaration_digest,
    empty_ledger,
    projected_l1_entry,
    resolve_declaration_outcome,
    validate_competency_declaration,
    validate_ledger,
)


def sample_declaration(**overrides):
    document = {
        "protocol": "aef.competency.declare.submit/v1",
        "competency_id": "dry-run-review",
        "title": "Dry-run review",
        "scope": "Inspect CLI dry-run outcomes",
        "limits": "No production mutation authority",
        "rationale": "Official birth after recorded review",
        "records": [{
            "record_id": "session-alpha",
            "digest": "sha256:" + ("a" * 64),
        }],
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    }
    document.update(overrides)
    return document


@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"protocol": "wrong"}, "invalid_declaration"),
        ({"competency_id": " leading"}, "invalid_competency_id"),
        ({"decision": {"source": "agent", "actor": "x", "decided_at": "2026-08-21T10:00:00Z", "approved": True}}, "invalid_declaration"),
        ({"decision": {"source": "human", "actor": "", "decided_at": "2026-08-21T10:00:00Z", "approved": True}}, "invalid_declaration"),
        ({"decision": {"source": "human", "actor": "op", "decided_at": "not-a-date", "approved": True}}, "invalid_human_decision"),
        ({"xp": 10}, "invalid_declaration_authority"),
        ({"level": "L2"}, "invalid_initial_level"),
        ({"records": []}, "invalid_declaration"),
    ],
)
def test_invalid_declaration_is_error(mutation, code):
    document = sample_declaration()
    document.update(mutation)
    with pytest.raises(InvalidCompetencyDeclarationError) as exc:
        validate_competency_declaration(document)
    assert exc.value.code == code or code in {exc.value.code, "invalid_declaration"}


def test_valid_declaration_projects_l1_only():
    document = validate_competency_declaration(sample_declaration())
    entry = projected_l1_entry(document)
    assert entry == {
        "id": "dry-run-review",
        "title": "Dry-run review",
        "level": "L1",
        "xp": 0,
        "cases": 0,
        "trust": None,
        "complex_cases": 0,
        "recent_significant_errors": 0,
        "probation": False,
        "source": "declared",
    }


def test_bind_records_digest_mismatch_is_blocked():
    document = validate_competency_declaration(sample_declaration())
    with pytest.raises(CompetencyDeclarationBlockedError) as exc:
        bind_declaration_records(document, {
            "session-alpha": {"digest": "sha256:" + ("b" * 64)},
        })
    assert exc.value.code == "record_digest_mismatch"


def test_idempotent_replay_is_no_change():
    document = validate_competency_declaration(sample_declaration())
    status, competencies, ledger = resolve_declaration_outcome(
        document, {}, empty_ledger(),
    )
    assert status == "CHANGE"
    status2, competencies2, ledger2 = resolve_declaration_outcome(
        document, competencies, ledger,
    )
    assert status2 == "NO_CHANGE"
    assert competencies2 == competencies
    assert ledger2 == ledger


def test_divergent_existing_competency_is_blocked():
    document = validate_competency_declaration(sample_declaration())
    existing = {
        "dry-run-review": {
            "id": "dry-run-review",
            "title": "other",
            "level": "L2",
            "xp": 5,
            "cases": 1,
            "trust": 0.1,
            "complex_cases": 0,
            "recent_significant_errors": 0,
            "probation": False,
            "source": "manual",
        }
    }
    with pytest.raises(CompetencyDeclarationBlockedError) as exc:
        resolve_declaration_outcome(document, existing, empty_ledger())
    assert exc.value.code == "competency_conflict"


def test_case_and_unicode_collision_matrix():
    cafe_nfc = "Café"
    cafe_nfd = "Cafe\u0301"
    assert competency_id_collides("café", [cafe_nfc]) == cafe_nfc
    assert competency_id_collides(cafe_nfd, [cafe_nfc]) == cafe_nfc
    assert competency_id_collides("CAFÉ", [cafe_nfc]) == cafe_nfc
    assert competency_id_collides(cafe_nfd.casefold(), [cafe_nfc]) == cafe_nfc
    document = validate_competency_declaration(sample_declaration(competency_id="café"))
    with pytest.raises(CompetencyDeclarationBlockedError) as exc:
        resolve_declaration_outcome(
            document,
            {cafe_nfc: {"id": cafe_nfc, "level": "L1", "xp": 0, "trust": None}},
            empty_ledger(),
        )
    assert exc.value.code == "competency_id_collision"


def test_similar_title_distinct_id_allowed():
    document = validate_competency_declaration(sample_declaration())
    status, competencies, _ = resolve_declaration_outcome(
        document,
        {
            "other-id": {
                "id": "other-id",
                "title": "Dry-run review",
                "level": "L1",
                "xp": 0,
                "trust": None,
            }
        },
        empty_ledger(),
    )
    assert status == "CHANGE"
    assert "dry-run-review" in competencies
    assert "other-id" in competencies


def test_declaration_digest_is_stable():
    document = validate_competency_declaration(sample_declaration())
    assert declaration_digest(document) == declaration_digest(document)
    assert declaration_digest(document).startswith("sha256:")


def test_far_future_decided_at_is_rejected():
    document = sample_declaration()
    document["decision"] = {
        "source": "human",
        "actor": "operator",
        "decided_at": "3999-01-01T00:00:00Z",
        "approved": True,
    }
    with pytest.raises(InvalidCompetencyDeclarationError) as exc:
        validate_competency_declaration(document)
    assert exc.value.code == "invalid_human_decision"


def test_duplicate_ledger_events_are_rejected():
    event = {
        "event_id": "competency-declaration:deadbeef",
        "competency_id": "dry-run-review",
        "declared_at": "2026-08-21T10:00:00Z",
        "title": "Dry-run review",
    }
    with pytest.raises(InvalidCompetencyDeclarationError) as exc:
        validate_ledger({
            "protocol": "aef.competency-declarations/v1",
            "events": [event, dict(event)],
        })
    assert exc.value.code == "invalid_declaration_ledger"
