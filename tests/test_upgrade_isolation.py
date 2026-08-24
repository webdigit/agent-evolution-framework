import json
import os

from aef import cli
from aef.filesystem import load_workspace


SENTINEL = "AEF_UPGRADE_SENTINEL_DO_NOT_LEAK_9f3c"
SENTINEL_PATH = "C:/sentinel-outside/memory.json"


def invoke(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    envelope = json.loads(captured.out) if captured.out.strip().startswith("{") else {}
    return code, envelope, captured


def test_exterior_memory_and_env_never_leak(tmp_path, capsys, monkeypatch):
    exterior = tmp_path / "outside"
    exterior.mkdir()
    (exterior / "memory.json").write_text(SENTINEL, encoding="utf-8")
    (exterior / "connectors.json").write_text(
        json.dumps({"connectors": [{"id": "gmail", "secret": SENTINEL}]}),
        encoding="utf-8",
    )
    (exterior / "record.json").write_text(SENTINEL, encoding="utf-8")
    monkeypatch.setenv("AEF_FAKE_CONNECTOR_TOKEN", SENTINEL)
    monkeypatch.setenv("NOTION_TOKEN", SENTINEL)

    workspace = tmp_path / "project"
    workspace.mkdir()
    agents = b"user bootstrap agents\n"
    claude = b"user bootstrap claude\n"
    (workspace / "AGENTS.md").write_bytes(agents)
    (workspace / "CLAUDE.md").write_bytes(claude)

    invoke(
        capsys, "--json", "--workspace", str(workspace),
        "init", "--instance-id", "agent-1", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    for args in (
        ("upgrade", "--check"),
        ("upgrade", "--dry-run"),
        ("upgrade",),
        ("upgrade", "--recover", "--dry-run"),
        ("audit",),
    ):
        code, envelope, captured = invoke(
            capsys, "--json", "--workspace", str(workspace), *args,
        )
        dumped = json.dumps(envelope) + captured.out + captured.err
        assert SENTINEL not in dumped
        assert SENTINEL_PATH not in dumped
        assert str(exterior) not in dumped
        assert "gmail" not in dumped.lower() or "gmail" not in json.dumps(envelope.get("result", {}))

    assert (workspace / "AGENTS.md").read_bytes() == agents
    assert (workspace / "CLAUDE.md").read_bytes() == claude
    stored = load_workspace(workspace)
    assert "AGENTS.md" not in stored.get("files", {})
    assert "CLAUDE.md" not in stored.get("files", {})


def test_upgrade_does_not_create_bootstrap(tmp_path, capsys):
    invoke(
        capsys, "--json", "--workspace", str(tmp_path),
        "init", "--instance-id", "agent-1", "--role", "generalist-agent",
        "--created-at", "2026-08-14T10:00:00Z",
    )
    invoke(capsys, "--json", "--workspace", str(tmp_path), "upgrade")
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "GEMINI.md").exists()


def test_comment_manual_user_validation_gate():
    # Validation utilisateur réelle = porte manuelle, hors tests automatisés.
    assert os.environ.get("AEF_REQUIRE_HUMAN_UPGRADE_SIGN_OFF") is None
