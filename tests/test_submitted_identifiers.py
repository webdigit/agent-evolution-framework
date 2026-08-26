import pytest
from pathlib import Path

from aef.identifiers import (
    COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE,
    DERIVED_IDENTIFIER_PREFIXES,
    SUBMITTED_IDENTIFIER_PATTERN,
    colon_message_if_present,
    validate_submitted_competency_id,
)
from aef.ingest_intake import InvalidIngestSubmissionError, validate_ingest_submission
from aef.record_document import InvalidRecordSubmissionError, validate_record_id
from aef.competency_declaration import (
    InvalidCompetencyDeclarationError,
    validate_competency_declaration,
)
from tests.support.derived_identifier_audit import (
    DERIVED_ID_AUDIT_EXCLUDED_FILES,
    audit_derived_prefixes_in_repository,
    audit_derived_prefixes_in_source,
    audit_excluded_files_remain_free_of_derived_prefixes,
    list_aef_module_relative_paths,
)

ROOT = Path(__file__).resolve().parents[1]


def test_submitted_identifier_pattern_disjoint_from_derived_prefixes():
    """
    Executable form of the Lot A arbitration for *submitted* identifiers.

    Proves the entry pattern cannot produce a string that collides with a
    registered derived prefix (signal:, observation:, …). It does **not** prove
    disjunction against persisted *state*: validate_competency_id (engine use)
    still accepts ``:``, so a brownfield workspace may already hold a legacy
    competency such as ``rule:x`` that collides with a derived namespace. That
    migration question is out of scope for this lot.
    """
    for prefix in DERIVED_IDENTIFIER_PREFIXES:
        example = f"{prefix}example-id"
        assert not SUBMITTED_IDENTIFIER_PATTERN.fullmatch(example)
        assert not SUBMITTED_IDENTIFIER_PATTERN.fullmatch(f"{prefix}a")


def test_derived_prefix_registry_covers_engine_literals():
    modules = list_aef_module_relative_paths(ROOT)
    assert modules
    assert DERIVED_ID_AUDIT_EXCLUDED_FILES <= modules

    gaps = audit_derived_prefixes_in_repository(ROOT)
    assert gaps == []

    exclusion_breaches = audit_excluded_files_remain_free_of_derived_prefixes(ROOT)
    assert exclusion_breaches == []


def test_derived_prefix_registry_gap_is_reported():
    fake_source = '''
def build_id():
    return f"novelty:{key}"
'''
    gaps = audit_derived_prefixes_in_source(fake_source)
    assert gaps == ["'novelty:' is not covered by DERIVED_IDENTIFIER_PREFIXES"]


def test_excluded_file_with_derived_literal_is_reported(tmp_path):
    package = tmp_path / "src" / "aef"
    package.mkdir(parents=True)
    excluded = package / "cli.py"
    excluded.write_text('MSG = "signal:should-not-hide-here"\n', encoding="utf-8")
    breaches = audit_excluded_files_remain_free_of_derived_prefixes(
        tmp_path,
        excluded_files=frozenset({"src/aef/cli.py"}),
    )
    assert breaches == [
        "src/aef/cli.py: excluded file contains derived literal "
        "'signal:should-not-hide-here'; remove it from DERIVED_ID_AUDIT_EXCLUDED_FILES"
    ]


def test_colon_in_record_id_rejects_with_reserved_separator_message():
    with pytest.raises(InvalidRecordSubmissionError) as raised:
        validate_record_id("email.support:echeance")
    assert raised.value.code == "invalid_record_id"
    assert raised.value.args[0] == COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE


def test_colon_in_ingest_pattern_key_rejects_with_reserved_separator_message():
    intake = {
        "protocol": "aef.ingest.submit/v1",
        "records": [
            {
                "record_id": "rec1",
                "digest": "sha256:" + "a" * 64,
                "events": [
                    {
                        "id": "evt1",
                        "novel": True,
                        "pattern_key": "email.support:echeance",
                    }
                ],
            }
        ],
    }
    with pytest.raises(InvalidIngestSubmissionError) as raised:
        validate_ingest_submission(intake)
    assert raised.value.code == "invalid_record_id"
    assert str(raised.value) == COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE


def test_colon_in_competency_declaration_rejects_with_reserved_separator_message():
    declaration = {
        "protocol": "aef.competency.declare.submit/v1",
        "competency_id": "skill:writing",
        "title": "Writing",
        "scope": "project",
        "limits": "none",
        "rationale": "needed",
        "records": [
            {
                "record_id": "rec1",
                "digest": "sha256:" + "b" * 64,
            }
        ],
        "decision": {
            "source": "human",
            "approved": True,
            "actor": "Alex",
            "decided_at": "2026-08-25T10:00:00Z",
        },
    }
    with pytest.raises(InvalidCompetencyDeclarationError) as raised:
        validate_competency_declaration(declaration)
    assert raised.value.code == "invalid_competency_id"
    assert str(raised.value) == COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE


def test_validate_submitted_competency_id_allows_non_submission_competency_rules():
    with pytest.raises(Exception):
        validate_submitted_competency_id("a\u202eb")

    assert colon_message_if_present("skill:writing") == COLON_IN_SUBMITTED_IDENTIFIER_MESSAGE
