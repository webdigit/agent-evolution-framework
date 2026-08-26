"""Lot 3 — Epic 4 INGEST: concurrency, provenance union, dry-run parity, scope."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pytest

from aef import cli
from aef.filesystem import EVALUATION_TRANSACTION_PATH
from aef.ingest_ops import (
    INGEST_CONFIRMATION_ANNOUNCEMENT,
    INGEST_DERIVED_ANNOUNCEMENT,
    INGEST_DERIVED_PREFIXES,
    INGEST_RULE_DERIVATION_ANNOUNCEMENT,
)
from aef.ingest_intake import InvalidIngestSubmissionError, validate_ingest_submission
from aef.record_document import build_persisted_record
from tests.test_cli_ingest import (
    init_workspace,
    intake_for,
    invoke,
    persist_sample_record,
    submission,
    write_json,
)

PREFIX_ANNOUNCEMENT_FRAGMENTS = {
    "signal:novelty:": "signal",
    "signal:repeated-help:": "signal",
    "signal:convergent-corrections:": "signal",
    "signal:rule-surprise:": "signal",
    "signal:unexplained-success:": "signal",
    "observation:signal:": "observation",
    "hypothesis:": "hypothes",
}


def _run_ingest_subprocess(workspace: Path, intake: Path) -> tuple[int, dict]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-m", "aef", "--json", "--workspace", str(workspace),
         "ingest", "--intake", str(intake)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    envelope = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.returncode, envelope


def test_concurrent_ingest_reports_only_persisted_changes(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intakes = []
    for index in range(8):
        intake = write_json(
            tmp_path / f"intake-{index}.json",
            intake_for(
                persisted,
                events=[{
                    "id": f"evt-{index:02d}",
                    "novel": True,
                    "pattern_key": f"pattern-{index:02d}",
                }],
            ),
        )
        intakes.append(intake)

    results: list[tuple[int, dict]] = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_run_ingest_subprocess, tmp_path, intake)
            for intake in intakes
        ]
        for future in as_completed(futures):
            results.append(future.result())

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    signal_count = len(knowledge.get("signals") or [])
    change_count = sum(
        1 for code, envelope in results
        if code == 0 and envelope.get("status") == "CHANGE"
    )
    assert signal_count == change_count == 8
    assert all(code in {0, 4} for code, _ in results)


def test_evidence_ids_union_across_sessions(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    alpha = persist_sample_record(tmp_path, capsys)
    beta_doc = submission(record_id="session-beta")
    beta = build_persisted_record(beta_doc)
    beta_recording = write_json(tmp_path / "beta.json", beta_doc)
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "record", "--recording", str(beta_recording),
    )
    assert code == 0 and envelope["status"] == "CHANGE"

    pattern = "shared-gap"
    first = write_json(
        tmp_path / "intake-alpha.json",
        intake_for(alpha, events=[{"id": "ea1", "novel": True, "pattern_key": pattern}]),
    )
    second = write_json(
        tmp_path / "intake-beta.json",
        {
            "protocol": "aef.ingest.submit/v1",
            "records": [{
                "record_id": beta["record_id"],
                "digest": beta["digest"],
                "events": [{"id": "eb1", "novel": True, "pattern_key": pattern}],
            }],
        },
    )
    for intake in (first, second):
        code, envelope, _ = invoke(
            capsys, "--json", "--workspace", str(tmp_path),
            "ingest", "--intake", str(intake),
        )
        assert code == 0 and envelope["status"] == "CHANGE"

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    signal = next(item for item in knowledge["signals"] if item["pattern_key"] == pattern)
    assert sorted(signal["evidence_ids"]) == ["ea1", "eb1"]
    sources = {entry["record_id"] for entry in signal["source_records"]}
    assert sources == {"session-alpha", "session-beta"}


def test_three_replays_are_idempotent(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    statuses = []
    for _ in range(3):
        code, envelope, _ = invoke(
            capsys, "--json", "--workspace", str(tmp_path),
            "ingest", "--intake", str(intake),
        )
        assert code == 0
        statuses.append(envelope["status"])
    assert statuses[0] == "CHANGE"
    assert statuses[1:] == ["NO_CHANGE", "NO_CHANGE"]


@pytest.mark.parametrize("journal_fixture", ["directory", "broken_symlink"])
def test_ingest_dry_run_matches_apply_when_evaluation_journal_blocks(
    tmp_path, capsys, journal_fixture,
):
    if journal_fixture == "broken_symlink" and os.name == "nt":
        pytest.skip("posix symlink witness")
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intake = write_json(tmp_path / "intake.json", intake_for(persisted))
    journal = tmp_path / EVALUATION_TRANSACTION_PATH
    journal.parent.mkdir(parents=True, exist_ok=True)
    if journal_fixture == "directory":
        journal.mkdir()
    else:
        journal.symlink_to(tmp_path / "missing-journal-target")

    dry_code, dry_env, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake), "--dry-run",
    )
    apply_code, apply_env, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    assert dry_code == apply_code == 4
    assert dry_env["status"] == apply_env["status"] == "BLOCKED"
    assert dry_env["meta"]["reason"] == apply_env["meta"]["reason"] == (
        "evaluation_recovery_required"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "a\x00b"),
        ("pattern_key", "a\u202eb"),
        ("id", "x" * 129),
    ],
)
def test_invalid_event_identifiers_are_rejected(field, value):
    event = {"id": "e1", "novel": True, "pattern_key": "gap"}
    event[field] = value
    document = {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": "session-alpha",
            "digest": "sha256:" + ("a" * 64),
            "events": [event],
        }],
    }
    with pytest.raises(InvalidIngestSubmissionError):
        validate_ingest_submission(document)


def test_ingest_derived_prefixes_must_be_announced_in_cli():
    assert frozenset(PREFIX_ANNOUNCEMENT_FRAGMENTS) == frozenset(INGEST_DERIVED_PREFIXES)
    announcement = INGEST_DERIVED_ANNOUNCEMENT.lower()
    for prefix, fragment in PREFIX_ANNOUNCEMENT_FRAGMENTS.items():
        assert prefix in INGEST_DERIVED_PREFIXES
        assert fragment in announcement
    parser = cli._build_parser()
    ingest = next(action for action in parser._actions if action.dest == "command")
    ingest_parser = ingest.choices["ingest"]
    assert INGEST_DERIVED_ANNOUNCEMENT in (ingest_parser.description or "")
    assert INGEST_CONFIRMATION_ANNOUNCEMENT in (ingest_parser.description or "")
    assert INGEST_RULE_DERIVATION_ANNOUNCEMENT in (ingest_parser.description or "")
