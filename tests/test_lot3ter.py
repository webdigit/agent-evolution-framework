"""Lot 3 ter — ingest availability under concurrency, lock git hygiene."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pytest

from aef.filesystem import (
    WORKSPACE_MUTATION_LOCK_FALLBACK_PATH,
    WORKSPACE_MUTATION_LOCK_PATH,
    WorkspaceContentionError,
    load_workspace,
)
from tests.test_cli_ingest import init_workspace, intake_for, invoke, persist_sample_record, write_json


def _run_ingest_subprocess(workspace: Path, intake: Path) -> tuple[int, dict]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable, "-m", "aef", "--json", "--workspace", str(workspace),
            "ingest", "--intake", str(intake),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    envelope = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.returncode, envelope


def test_concurrent_ingest_never_reports_filesystem_error(tmp_path, capsys):
    """Red test for B1: legitimate concurrent ingest must not exit 6 / ERROR."""
    init_workspace(tmp_path, capsys)
    persisted = persist_sample_record(tmp_path, capsys)
    intakes = []
    for index in range(8):
        intakes.append(write_json(
            tmp_path / f"intake-{index:02d}.json",
            intake_for(
                persisted,
                events=[{
                    "id": f"evt-{index:02d}",
                    "novel": True,
                    "pattern_key": f"pattern-{index:02d}",
                }],
            ),
        ))

    failures = 0
    for _round in range(25):
        with ProcessPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(_run_ingest_subprocess, tmp_path, intake)
                for intake in intakes
            ]
            for future in as_completed(futures):
                code, envelope = future.result()
                if code == 6 or envelope.get("status") == "ERROR":
                    failures += 1
                assert code in {0, 4}
                assert envelope.get("status") in {"CHANGE", "BLOCKED", "NO_CHANGE"}

    assert failures == 0


def test_init_does_not_leave_fallback_lock_at_root(tmp_path, capsys):
    init_workspace(tmp_path, capsys)
    assert not (tmp_path / WORKSPACE_MUTATION_LOCK_FALLBACK_PATH).exists()
    assert (tmp_path / ".gitignore").is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_init_lock_files_are_gitignored(tmp_path, capsys):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    init_workspace(tmp_path, capsys)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert WORKSPACE_MUTATION_LOCK_FALLBACK_PATH not in status
    assert WORKSPACE_MUTATION_LOCK_PATH not in status
    assert ".gitignore" in status


def test_load_workspace_skips_atomic_write_temporaries(tmp_path):
    agent = tmp_path / ".agent" / "state"
    agent.mkdir(parents=True)
    target = agent / "value.json"
    target.write_text('{"value": "stable"}\n', encoding="utf-8")
    transient = agent / ".value.json.abc123.tmp"
    transient.write_text('{"value": "draft"}\n', encoding="utf-8")

    project = load_workspace(tmp_path)
    assert project["files"] == {".agent/state/value.json": {"value": "stable"}}


def test_load_workspace_missing_governed_file_raises_contention(tmp_path, monkeypatch):
    agent = tmp_path / ".agent" / "state"
    agent.mkdir(parents=True)
    target = agent / "value.json"
    target.write_text('{"value": "stable"}\n', encoding="utf-8")
    original_read_text = Path.read_text

    def read_text(self, encoding="utf-8"):
        if self == target:
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return original_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(WorkspaceContentionError):
        load_workspace(tmp_path)
