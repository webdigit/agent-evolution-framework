"""Lot 3 bis — transactional lock coverage, evidence cap honesty, lock path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pytest

from aef.filesystem import WORKSPACE_MUTATION_LOCK_PATH
from aef.knowledge import MAX_EVIDENCE_IDS
from tests.test_cli_competency_declare import (
    declaration_for,
    init_workspace,
    invoke,
    persist_sample_record,
    write_json,
)
from tests.test_cli_ingest import intake_for, persist_sample_record as ingest_persist


def _run_competency_declare_subprocess(workspace: Path, declaration: Path) -> tuple[int, dict]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable, "-m", "aef", "--json", "--workspace", str(workspace),
            "competency", "declare", "--declaration", str(declaration),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    envelope = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.returncode, envelope


def test_concurrent_competency_declare_matches_persisted_count(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    declarations = []
    for index in range(8):
        declaration = write_json(
            tmp_path / f"declaration-{index}.json",
            declaration_for(
                persisted,
                competency_id=f"concurrent-{index:02d}",
                title=f"Concurrent {index:02d}",
            ),
        )
        declarations.append(declaration)

    results: list[tuple[int, dict]] = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_run_competency_declare_subprocess, tmp_path, declaration)
            for declaration in declarations
        ]
        for future in as_completed(futures):
            results.append(future.result())

    competencies = json.loads(
        (tmp_path / ".agent/state/competencies.json").read_text(encoding="utf-8"),
    )
    change_count = sum(
        1 for code, envelope in results
        if code == 0 and envelope.get("status") == "CHANGE"
    )
    assert len(competencies) == change_count == 8
    assert all(code in {0, 4} for code, _ in results)


def test_sequential_competency_declare_control_positive(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    change_count = 0
    for index in range(8):
        declaration = write_json(
            tmp_path / f"seq-{index}.json",
            declaration_for(
                persisted,
                competency_id=f"sequential-{index:02d}",
                title=f"Sequential {index:02d}",
            ),
        )
        code, envelope, _ = invoke(
            capsys, "--json", "--workspace", str(tmp_path),
            "competency", "declare", "--declaration", str(declaration),
        )
        assert code == 0 and envelope["status"] == "CHANGE"
        change_count += 1
    competencies = json.loads(
        (tmp_path / ".agent/state/competencies.json").read_text(encoding="utf-8"),
    )
    assert len(competencies) == change_count == 8


def test_evidence_cap_blocks_instead_of_silent_no_change(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = ingest_persist(tmp_path, capsys)
    pattern = "cap-test"
    for index in range(MAX_EVIDENCE_IDS):
        intake = write_json(
            tmp_path / f"intake-{index:03d}.json",
            intake_for(
                persisted,
                events=[{
                    "id": f"evt-{index:03d}",
                    "novel": True,
                    "pattern_key": pattern,
                }],
            ),
        )
        code, envelope, _ = invoke(
            capsys, "--json", "--workspace", str(tmp_path),
            "ingest", "--intake", str(intake),
        )
        assert code == 0 and envelope["status"] == "CHANGE"

    overflow = write_json(
        tmp_path / "intake-overflow.json",
        intake_for(
            persisted,
            events=[{
                "id": "evt-overflow",
                "novel": True,
                "pattern_key": pattern,
            }],
        ),
    )
    code, envelope, _ = invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(overflow),
    )
    assert code == 4
    assert envelope["status"] == "BLOCKED"
    assert envelope["meta"]["reason"] == "evidence_cap_exceeded"

    knowledge = json.loads(
        (tmp_path / ".agent/knowledge/knowledge.json").read_text(encoding="utf-8"),
    )
    signal = next(item for item in knowledge["signals"] if item["pattern_key"] == pattern)
    assert len(signal["evidence_ids"]) == MAX_EVIDENCE_IDS
    assert "evt-overflow" not in signal["evidence_ids"]


def test_workspace_mutation_lock_lives_under_agent_state(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    persisted = ingest_persist(tmp_path, capsys)
    intake = write_json(
        tmp_path / "intake.json",
        intake_for(persisted, events=[{"id": "e1", "novel": True, "pattern_key": "gap"}]),
    )
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "ingest", "--intake", str(intake),
    )
    lock_path = tmp_path / WORKSPACE_MUTATION_LOCK_PATH
    assert lock_path.is_file()
    assert ".agent" in lock_path.parts
    assert not (tmp_path / ".aef-workspace-mutation.lock").exists()
