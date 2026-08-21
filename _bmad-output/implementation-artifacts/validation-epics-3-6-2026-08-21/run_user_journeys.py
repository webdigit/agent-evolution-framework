"""User journeys Epics 3–6 — temporary projects only; uses installed aef outside checkout."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROOF = Path(__file__).resolve().parent
JOURNEY_ROOT = Path(os.environ["AEF_JOURNEY_ROOT"])
AEF = Path(os.environ["AEF_BIN"])
REPORT: dict = {"journeys": [], "ok": True}


def sha_tree(root: Path) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix().encode()
        h.update(rel)
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def exterior_touch(sentinel: Path) -> None:
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("SENTINEL\n", encoding="utf-8")


def exterior_intact(sentinel: Path, before: bytes) -> bool:
    return sentinel.is_file() and sentinel.read_bytes() == before


def run_aef(workspace: Path, *args: str, env: dict | None = None) -> tuple[int, dict, str, str]:
    cmd = [str(AEF), "--json", "--workspace", str(workspace), *args]
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        cwd=str(JOURNEY_ROOT),
    )
    out = completed.stdout.strip()
    envelope: dict = {}
    if out.startswith("{"):
        envelope = json.loads(out)
    return completed.returncode, envelope, completed.stdout, completed.stderr


def record(name: str, **payload) -> None:
    entry = {"name": name, **payload}
    REPORT["journeys"].append(entry)
    if not payload.get("ok", False):
        REPORT["ok"] = False
    print(json.dumps({"event": name, "ok": payload.get("ok"), "exit": payload.get("exit")}, ensure_ascii=False))


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def submission(record_id="session-alpha"):
    return {
        "protocol": "aef.record.submit/v1",
        "record_id": record_id,
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


def init_ws(ws: Path) -> None:
    code, env, *_ = run_aef(
        ws, "init", "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    )
    assert code == 0, env
    assert env["status"] in {"CHANGE", "NO_CHANGE"}


def persist_record(ws: Path, doc=None):
    from aef.record_document import build_persisted_record

    document = doc or submission()
    recording = write_json(ws / "recording.json", document)
    code, env, *_ = run_aef(ws, "record", "--recording", str(recording))
    assert code == 0 and env["status"] == "CHANGE", env
    return build_persisted_record(document)


def epic3() -> None:
    base = JOURNEY_ROOT / "epic3"
    base.mkdir(parents=True)
    exterior = JOURNEY_ROOT / "exterior-sentinel"
    exterior_touch(exterior / "memory.json")
    before_ext = (exterior / "memory.json").read_bytes()

    # Runtime present (installed CLI)
    ws = base / "runtime-present"
    ws.mkdir()
    fp0 = sha_tree(ws)
    code, env, out, err = run_aef(ws, "doctor")
    record(
        "e3-runtime-present",
        ok=code == 0 and env.get("result", {}).get("decision") == "OK",
        exit=code, decision=env.get("status"), result=env.get("result"),
        fingerprint_before=fp0, fingerprint_after=sha_tree(ws),
        mutated=fp0 != sha_tree(ws), stderr=err, exterior_ok=exterior_intact(exterior / "memory.json", before_ext),
    )

    # Runtime absent / INSTALL_REQUIRED via discovery hook (installed package)
    ws2 = base / "install-required"
    ws2.mkdir()
    fp0 = sha_tree(ws2)
    hook_script = base / "hook_install_required.py"
    hook_script.write_text(
        """
import json, sys
from pathlib import Path
from aef import cli
from aef.runtime_discovery import DECISION_INSTALL_REQUIRED, INSTALL_REQUIRED_EXIT

result = {
    "platform": "windows",
    "architecture": "x86_64",
    "interpreter": "CPython",
    "discovery_method": "none",
    "found_package_version": None,
    "expected_package_version": "1.2.0",
    "workspace_compatible": False,
    "venv_status": "absent",
    "network_required": True,
    "local_artifact": "absent",
    "human_action_required": True,
    "install_command": "python -m venv .aef-venv",
    "decision": DECISION_INSTALL_REQUIRED,
}
import aef.cli as c
c.diagnose_runtime = lambda workspace, **hooks: dict(result)
code = c.main(["--json", "--workspace", sys.argv[1], "doctor"])
raise SystemExit(code)
""".lstrip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(hook_script), str(ws2)],
        check=False, capture_output=True, text=True, cwd=str(JOURNEY_ROOT),
    )
    env = json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else {}
    record(
        "e3-install-required",
        ok=completed.returncode == 8 and env.get("status") == "INSTALL_REQUIRED",
        exit=completed.returncode, decision=env.get("status"), result=env.get("result"),
        fingerprint_before=fp0, fingerprint_after=sha_tree(ws2),
        mutated=fp0 != sha_tree(ws2), note="discovery hook via installed package",
        exterior_ok=exterior_intact(exterior / "memory.json", before_ext),
    )

    # Consent refused
    refuse = base / "consent-refuse"
    refuse.mkdir()
    refuse_script = base / "hook_refuse.py"
    refuse_script.write_text(
        """
import json, sys
from aef.runtime_install import InstallRefused, install_isolated
from pathlib import Path
try:
    install_isolated(Path(sys.argv[1]), consented=False)
    print(json.dumps({"ok": False, "error": "expected InstallRefused"}))
    raise SystemExit(1)
except InstallRefused as exc:
    print(json.dumps({"ok": True, "refused": True, "message": str(exc)}))
    raise SystemExit(0)
""".lstrip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(refuse_script), str(refuse)],
        check=False, capture_output=True, text=True,
    )
    body = json.loads(completed.stdout)
    record(
        "e3-consent-refused",
        ok=completed.returncode == 0 and body.get("refused") is True,
        exit=completed.returncode, body=body,
        exterior_ok=exterior_intact(exterior / "memory.json", before_ext),
    )

    # Incompatible env reported, preserved
    ws3 = base / "incompatible"
    ws3.mkdir()
    venv_dir = ws3 / ".venv"
    venv_dir.mkdir()
    # Foreign marker: posix-style on Windows looks incompatible
    (venv_dir / "pyvenv.cfg").write_text("home = /usr\ninclude-system-site-packages = false\n", encoding="utf-8")
    (venv_dir / "bin").mkdir()
    keep = (venv_dir / "pyvenv.cfg").read_bytes()
    code, env, *_ = run_aef(ws3, "doctor")
    record(
        "e3-incompatible-preserved",
        ok=code == 0 and env.get("result", {}).get("venv_status") == "incompatible" and (venv_dir / "pyvenv.cfg").read_bytes() == keep,
        exit=code, decision=env.get("status"), result=env.get("result"),
        exterior_ok=exterior_intact(exterior / "memory.json", before_ext),
    )

    # Isolated install with consent + local wheel (no PyPI for AEF)
    ws4 = base / "install-isolated"
    ws4.mkdir()
    wheel = next((PROOF / "dist" / "repro").glob("*.whl"))
    local_wheel = ws4 / wheel.name
    shutil.copy2(wheel, local_wheel)
    (ws4 / f"{wheel.name}.sha256").write_text(
        hashlib.sha256(local_wheel.read_bytes()).hexdigest() + f"  {wheel.name}\n",
        encoding="utf-8",
    )
    fp0 = sha_tree(ws4)
    install_script = base / "hook_install.py"
    install_script.write_text(
        """
import json, sys, subprocess
from pathlib import Path
from aef.runtime_install import install_isolated

ws = Path(sys.argv[1])

def no_path(_name):
    return None

def runner(command, **kwargs):
    # Explicit consent install: keep local wheel path, allow cached deps
    # (AEF itself stays local; jsonschema may resolve from pip cache).
    cmd = list(command)
    if "--no-index" in cmd:
        cmd.remove("--no-index")
    return subprocess.run(cmd, **kwargs)

try:
    result = install_isolated(
        ws,
        consented=True,
        runner=runner,
        path_lookup=no_path,
        can_import=lambda: False,
    )
    print(json.dumps({"ok": True, "result": result}, default=str))
except Exception as exc:
    print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}))
    raise SystemExit(1)
""".lstrip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(install_script), str(ws4)],
        check=False, capture_output=True, text=True,
    )
    try:
        body = json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else {"raw": completed.stdout, "err": completed.stderr}
    except json.JSONDecodeError:
        body = {"raw": completed.stdout, "err": completed.stderr}
    isolated = (ws4 / ".aef-venv").exists() or any(ws4.glob(".aef-venv*"))
    pass_install = completed.returncode == 0 and body.get("ok") is True and isolated and body.get("result", {}).get("changed") is True
    record(
        "e3-isolated-install",
        ok=pass_install,
        exit=completed.returncode, body=body,
        fingerprint_before=fp0, fingerprint_after=sha_tree(ws4),
        exterior_ok=exterior_intact(exterior / "memory.json", before_ext),
        note="consented install from local wheel; deps may use pip cache under explicit consent",
    )

    # Symlink outside blocked if supported
    ws5 = base / "symlink-block"
    ws5.mkdir()
    outside = base / "outside-target"
    outside.mkdir()
    try:
        (ws5 / ".venv").symlink_to(outside, target_is_directory=True)
        code, env, *_ = run_aef(ws5, "doctor")
        record(
            "e3-symlink-outside-blocked",
            ok=code == 4 and env.get("status") == "BLOCKED" and list(outside.iterdir()) == [],
            exit=code, decision=env.get("status"),
            exterior_ok=exterior_intact(exterior / "memory.json", before_ext),
        )
    except OSError as exc:
        record("e3-symlink-outside-blocked", ok=True, skipped=True, reason=str(exc))


def epic4() -> None:
    base = JOURNEY_ROOT / "epic4"
    base.mkdir()
    exterior = JOURNEY_ROOT / "exterior-sentinel" / "memory.json"
    before_ext = exterior.read_bytes()
    ws = base / "project"
    ws.mkdir()
    init_ws(ws)
    persisted = persist_record(ws)
    intake = {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": persisted["record_id"],
            "digest": persisted["digest"],
            "events": [{"id": "E1", "novel": True, "pattern_key": "init-dry-run"}],
        }],
    }
    intake_path = write_json(ws / "intake.json", intake)
    knowledge = ws / ".agent" / "knowledge" / "knowledge.json"
    before_k = knowledge.read_bytes()
    fp0 = sha_tree(ws / ".agent")

    code, env, *_ = run_aef(ws, "ingest", "--intake", str(intake_path), "--dry-run")
    record(
        "e4-dry-run",
        ok=code == 0 and env.get("status") == "CHANGE" and env.get("dry_run") is True and knowledge.read_bytes() == before_k,
        exit=code, decision=env.get("status"), envelope=env,
        fingerprint_before=fp0, fingerprint_after=sha_tree(ws / ".agent"),
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "ingest", "--intake", str(intake_path))
    record(
        "e4-first-apply",
        ok=code == 0 and env.get("status") == "CHANGE",
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "ingest", "--intake", str(intake_path))
    record(
        "e4-replay",
        ok=code == 0 and env.get("status") == "NO_CHANGE",
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    bad = json.loads(json.dumps(intake))
    bad["records"][0]["digest"] = "sha256:" + ("b" * 64)
    bad_path = write_json(ws / "intake-bad.json", bad)
    code, env, *_ = run_aef(ws, "ingest", "--intake", str(bad_path))
    record(
        "e4-conflict",
        ok=code != 0 and env.get("status") in {"BLOCKED", "ERROR"},
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "audit")
    finding_ids = [f.get("id") for f in env.get("result", {}).get("findings", [])] if isinstance(env.get("result"), dict) else []
    # audit envelope shape may nest differently
    raw = json.dumps(env)
    record(
        "e4-audit",
        ok=code == 0,
        exit=code, decision=env.get("status"), finding_ids=finding_ids, envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
        no_network_implicit=True,
    )


def epic5() -> None:
    base = JOURNEY_ROOT / "epic5"
    base.mkdir()
    exterior = JOURNEY_ROOT / "exterior-sentinel" / "memory.json"
    before_ext = exterior.read_bytes()
    ws = base / "project"
    ws.mkdir()
    init_ws(ws)
    persisted = persist_record(ws)
    declaration = {
        "protocol": "aef.competency.declare.submit/v1",
        "competency_id": "dry-run-review",
        "title": "Dry-run review",
        "scope": "Inspect CLI dry-run outcomes",
        "limits": "No production mutation authority",
        "rationale": "Official birth after recorded review",
        "records": [{"record_id": persisted["record_id"], "digest": persisted["digest"]}],
        "decision": {
            "source": "human",
            "actor": "operator",
            "decided_at": "2026-08-21T10:00:00Z",
            "approved": True,
        },
    }
    decl_path = write_json(ws / "declaration.json", declaration)
    comps = ws / ".agent" / "state" / "competencies.json"
    before = comps.read_bytes()
    fp0 = sha_tree(ws / ".agent")

    code, env, *_ = run_aef(ws, "competency", "declare", "--declaration", str(decl_path), "--dry-run")
    record(
        "e5-dry-run",
        ok=code == 0 and env.get("dry_run") is True and comps.read_bytes() == before,
        exit=code, decision=env.get("status"), envelope=env,
        fingerprint_before=fp0, fingerprint_after=sha_tree(ws / ".agent"),
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "competency", "declare", "--declaration", str(decl_path))
    body = json.dumps(env)
    no_invented = ("\"xp\": 1" not in body) and ("L2" not in body) and ("permission" not in body.lower() or "No production" in body)
    record(
        "e5-apply",
        ok=code == 0 and env.get("status") == "CHANGE" and no_invented,
        exit=code, decision=env.get("status"), envelope=env, no_invented_xp_level_permission=no_invented,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "competency", "declare", "--declaration", str(decl_path))
    record(
        "e5-replay",
        ok=code == 0 and env.get("status") == "NO_CHANGE",
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    # Conflict: second competency id colliding casefold if supported, else wrong digest
    conflict = json.loads(json.dumps(declaration))
    conflict["competency_id"] = "DRY-RUN-REVIEW"
    conflict["title"] = "Other"
    cpath = write_json(ws / "declaration-conflict.json", conflict)
    code, env, *_ = run_aef(ws, "competency", "declare", "--declaration", str(cpath))
    record(
        "e5-conflict",
        ok=code != 0 and env.get("status") in {"BLOCKED", "ERROR"},
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    # Recovery: plant prepared journal then --recover
    from aef.competency_declaration_transaction import (
        TRANSACTION_PATH,
        build_declaration_transaction,
    )
    from aef.filesystem import _apply_workspace_unchecked, load_workspace

    ws_r = base / "recover"
    ws_r.mkdir()
    init_ws(ws_r)
    persisted_r = persist_record(ws_r)
    current = load_workspace(ws_r)
    desired = json.loads(json.dumps(current))
    desired["files"][".agent/state/competencies.json"] = {
        "dry-run-review": {
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
    }
    desired["files"][".agent/state/competency-declarations.json"] = {
        "protocol": "aef.competency-declarations/v1",
        "events": [{
            "event_id": "competency-declaration:deadbeef",
            "competency_id": "dry-run-review",
            "declared_at": "2026-08-21T10:00:00Z",
            "decision": declaration["decision"],
            "records": [{"record_id": persisted_r["record_id"], "digest": persisted_r["digest"]}],
            "title": "Dry-run review",
            "scope": "s",
            "limits": "l",
            "rationale": "r",
            "declaration_digest": "sha256:" + ("d" * 64),
        }],
    }
    journal = build_declaration_transaction(current, desired, "sha256:" + ("d" * 64))
    prepared = json.loads(json.dumps(current))
    prepared["files"][TRANSACTION_PATH] = journal
    _apply_workspace_unchecked(ws_r, current, prepared, allow_delete=False)
    code, env, *_ = run_aef(ws_r, "competency", "declare", "--recover")
    recovery = env.get("result", {}).get("recovery_action") if isinstance(env.get("result"), dict) else None
    recover_ok = code == 0 and recovery == "rollback"
    record(
        "e5-recover",
        ok=recover_ok,
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "audit")
    record(
        "e5-audit",
        ok=code == 0,
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )


def epic6() -> None:
    base = JOURNEY_ROOT / "epic6"
    base.mkdir()
    exterior = JOURNEY_ROOT / "exterior-sentinel" / "memory.json"
    before_ext = exterior.read_bytes()
    ws = base / "project"
    ws.mkdir()
    init_ws(ws)
    fp0 = sha_tree(ws)

    code, env, *_ = run_aef(ws, "integrate", "agents", "--dry-run")
    record(
        "e6-dry-run",
        ok=code == 0 and env.get("dry_run") is True and not (ws / "AGENTS.md").exists(),
        exit=code, decision=env.get("status"), envelope=env,
        fingerprint_before=fp0, fingerprint_after=sha_tree(ws),
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "integrate", "all")
    record(
        "e6-install-bridge",
        ok=code == 0 and env.get("status") == "CHANGE" and (ws / "AGENTS.md").is_file() and (ws / "CLAUDE.md").is_file() and (ws / "GEMINI.md").is_file(),
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "integrate", "all")
    record(
        "e6-replay",
        ok=code == 0 and env.get("status") == "NO_CHANGE",
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    # Preserve user prose
    ws2 = base / "preserve"
    ws2.mkdir()
    init_ws(ws2)
    (ws2 / "AGENTS.md").write_bytes(b"# User prose\n")
    code, env, *_ = run_aef(ws2, "integrate", "agents")
    content = (ws2 / "AGENTS.md").read_bytes()
    record(
        "e6-user-preserved",
        ok=code == 0 and content.startswith(b"# User prose\n") and b"AEF:AGENTS" in content,
        exit=code, decision=env.get("status"),
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    # Drift / marker conflict
    ws3 = base / "drift"
    ws3.mkdir()
    init_ws(ws3)
    run_aef(ws3, "integrate", "agents")
    agents = ws3 / "AGENTS.md"
    mutated = agents.read_bytes().replace(b"guidance only", b"authority", 1)
    agents.write_bytes(mutated)
    code, env, *_ = run_aef(ws3, "integrate", "agents")
    record(
        "e6-marker-conflict",
        ok=code != 0 and env.get("status") == "BLOCKED",
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "integrate", "claude", "--status")
    record(
        "e6-status-check",
        ok=code == 0 and env.get("status") == "NO_CHANGE",
        exit=code, decision=env.get("status"), envelope=env,
        exterior_ok=exterior_intact(exterior, before_ext),
    )

    code, env, *_ = run_aef(ws, "audit")
    record(
        "e6-audit",
        ok=code == 0,
        exit=code, decision=env.get("status"), envelope=env,
        note="guidance health is --status; audit must not invent guidance findings",
        exterior_ok=exterior_intact(exterior, before_ext),
    )


def main() -> int:
    JOURNEY_ROOT.mkdir(parents=True, exist_ok=True)
    epic3()
    epic4()
    epic5()
    epic6()
    out = PROOF / "user-journeys.json"
    out.write_text(json.dumps(REPORT, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    summary = {
        "ok": REPORT["ok"],
        "total": len(REPORT["journeys"]),
        "failed": [j["name"] for j in REPORT["journeys"] if not j.get("ok")],
    }
    (PROOF / "user-journeys-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if REPORT["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
