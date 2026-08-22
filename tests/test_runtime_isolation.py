from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aef import cli
from aef.runtime_discovery import discover_runtime
from tests.test_runtime_discovery import write_venv


SENTINEL = "AEF_RUNTIME_SENTINEL_DO_NOT_LEAK_7c1e"
SENTINEL_PATH = "C:/sentinel-outside/memory.json"


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def test_doctor_never_leaks_exterior_sentinels(tmp_path, capsys, monkeypatch):
    exterior = tmp_path / "outside"
    exterior.mkdir()
    (exterior / "memory.json").write_text(SENTINEL, encoding="utf-8")
    (exterior / "connectors.json").write_text(
        json.dumps({"connectors": [{"id": "gmail", "secret": SENTINEL}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEF_FAKE_CONNECTOR_TOKEN", SENTINEL)
    monkeypatch.setenv("NOTION_TOKEN", SENTINEL)
    workspace = tmp_path / "project with spaces"
    workspace.mkdir()
    write_venv(workspace / ".venv", kind="windows")
    (workspace / "AGENTS.md").write_text("user bootstrap\n", encoding="utf-8")

    code, envelope, captured = invoke(
        capsys, "--json", "--workspace", str(workspace), "doctor",
    )
    dumped = json.dumps(envelope) + captured.out + captured.err
    assert SENTINEL not in dumped
    assert SENTINEL_PATH not in dumped
    assert str(exterior) not in dumped
    assert str(Path.home()) not in dumped
    assert code == 0
    assert envelope["status"] == "PASS"
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "user bootstrap\n"
    assert list(exterior.iterdir())


def test_foreign_venv_is_never_executed(tmp_path, monkeypatch):
    foreign = "windows" if os.name != "nt" else "posix"
    root = write_venv(tmp_path / ".venv", kind=foreign)
    exe = root / "Scripts" / "python.exe"
    posix = root / "bin" / "python"
    if exe.is_file():
        exe.write_bytes(b"must-not-run")
    if posix.is_file():
        posix.write_bytes(b"must-not-run")

    discovered = discover_runtime(tmp_path, can_import=lambda: False)
    assert discovered["venv_status"] == "incompatible"
    if exe.is_file():
        assert exe.read_bytes() == b"must-not-run"
    if posix.is_file():
        assert posix.read_bytes() == b"must-not-run"


def test_runtime_instruction_contract():
    text = Path("docs/runtime.md").read_text(encoding="utf-8")
    assert "INSTALL_REQUIRED" in text
    assert ".agent/state/" in text
    assert "python -m venv" in text
    assert "python -m pip" in text or "python -m aef" in text
    assert "aef --json doctor" in text
    assert "manually" in text.lower()
    assert "--install" not in text
    lowered = text.lower()
    for vendor in ("claude", "chatgpt", "gemini", "cursor", "cowork", "openai"):
        assert vendor not in lowered
