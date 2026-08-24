import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, verifier_arbre_importe, finir
verifier_arbre_importe()


"""A non-AEF thread rewrites a governed file during a real mutating CLI call.

Positive control: the same command without that thread reports CHANGE and
persists. Failure: CHANGE while a concurrent write is overwritten, or an
active writer never surfaces workspace_contention (the apply-time guard is
dead).
"""
import json, subprocess, sys, tempfile, shutil, threading, time, uuid
from pathlib import Path
sys.path.insert(0, str(ROOT / "src"))
from aef.record_document import build_persisted_record

AEF = AEF
REGISTRY = Path(".agent") / "integrations" / "registry.json"
ROUNDS = 40
TOKEN_KEY = "external_writer"


def cli(ws, *args, timeout=120):
    result = subprocess.run(
        [AEF, "--json", "--workspace", str(ws), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        envelope = json.loads(result.stdout)
    except Exception:
        envelope = {}
    return result.returncode, envelope.get("status"), (envelope.get("meta") or {}).get("reason"), envelope


def submission(record_id="session-alpha"):
    return {
        "protocol": "aef.record.submit/v1",
        "record_id": record_id,
        "recorded_at": "2026-08-20T13:21:00Z",
        "declared_by": {"kind": "human", "identifier": "operator"},
        "payload": {
            "context": "reviewed a failed dry-run",
            "actions": [{"summary": "inspected the CLI envelope"}],
            "outcomes": [], "incidents": [], "evidence": [],
        },
    }


def intake_for(persisted, events):
    return {
        "protocol": "aef.ingest.submit/v1",
        "records": [{
            "record_id": persisted["record_id"],
            "digest": persisted["digest"],
            "events": events,
        }],
    }


def setup(prefix, *, record=True):
    ws = Path(tempfile.mkdtemp(prefix=prefix))
    code, status, _, _ = cli(
        ws, "init",
        "--instance-id", "agent-1",
        "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z",
    )
    assert status in {"CHANGE", "NO_CHANGE"}, status
    if not record:
        return ws, None
    doc = submission()
    (ws / "recording.json").write_text(json.dumps(doc), encoding="utf-8")
    code, status, _, _ = cli(ws, "record", "--recording", str(ws / "recording.json"))
    assert status == "CHANGE", status
    return ws, build_persisted_record(doc)


def sneak_loop(path, payload, stop, writes):
    while not stop.is_set():
        try:
            path.write_text(payload, encoding="utf-8")
            writes.append(1)
        except OSError:
            pass
        time.sleep(0.001)


def with_writer(ws, argv):
    token = uuid.uuid4().hex
    payload = json.dumps({"connectors": [], TOKEN_KEY: token}, indent=2) + "\n"
    target = ws / REGISTRY
    stop = threading.Event()
    writes = []
    thread = threading.Thread(
        target=sneak_loop, args=(target, payload, stop, writes), daemon=True,
    )
    thread.start()
    try:
        code, status, reason, envelope = cli(ws, *argv)
    finally:
        stop.set()
        thread.join(timeout=2)
    after = target.read_text(encoding="utf-8") if target.is_file() else ""
    return code, status, reason, writes, after, token


def persist_ingest(ws):
    knowledge = ws / ".agent" / "knowledge" / "knowledge.json"
    if not knowledge.is_file():
        return False
    body = json.loads(knowledge.read_text(encoding="utf-8"))
    return bool(body.get("signals"))


def persist_record(ws):
    records = ws / ".agent" / "records"
    return records.is_dir() and any(records.glob("*.json"))


def overwritten(writes, after, token):
    return bool(writes) and TOKEN_KEY not in after and token not in after


def positive_ingest():
    ws, persisted = setup("ext-pos-ing-")
    intake = ws / "intake.json"
    intake.write_text(json.dumps(intake_for(persisted, [
        {"id": "evt-pos", "novel": True, "pattern_key": "pattern-pos"},
    ])), encoding="utf-8")
    code, status, _, _ = cli(ws, "ingest", "--intake", str(intake))
    held = status == "CHANGE" and persist_ingest(ws)
    print("  CONTRÔLE POSITIF ingest : status=%s persisté=%s" % (status, persist_ingest(ws)))
    shutil.rmtree(ws, ignore_errors=True)
    return held


def positive_record():
    ws, _ = setup("ext-pos-rec-", record=False)
    doc = submission("session-positive-record")
    rec = ws / "recording.json"
    rec.write_text(json.dumps(doc), encoding="utf-8")
    code, status, _, _ = cli(ws, "record", "--recording", str(rec))
    held = status == "CHANGE" and persist_record(ws)
    print("  CONTRÔLE POSITIF record : status=%s persisté=%s" % (status, persist_record(ws)))
    shutil.rmtree(ws, ignore_errors=True)
    return held


def rounds(kind):
    blocked = change = crushed = other = 0
    reasons = {}
    for i in range(ROUNDS):
        if kind == "ingest":
            ws, persisted = setup("ext-ing-")
            intake = ws / "intake.json"
            intake.write_text(json.dumps(intake_for(persisted, [
                {"id": "evt-%03d" % i, "novel": True, "pattern_key": "pattern-%03d" % i},
            ])), encoding="utf-8")
            argv = ["ingest", "--intake", str(intake)]
        else:
            ws, _ = setup("ext-rec-", record=False)
            rec = ws / "recording.json"
            rec.write_text(json.dumps(submission("session-ext-%03d" % i)), encoding="utf-8")
            argv = ["record", "--recording", str(rec)]
        code, status, reason, writes, after, token = with_writer(ws, argv)
        if status == "BLOCKED" and reason == "workspace_contention":
            blocked += 1
            reasons[reason] = reasons.get(reason, 0) + 1
        elif status == "CHANGE":
            change += 1
            if overwritten(writes, after, token):
                crushed += 1
                print("  *** ÉCART %s tour %d : CHANGE et écriture tiers écrasée ***" % (kind, i))
        else:
            other += 1
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        shutil.rmtree(ws, ignore_errors=True)
    print(
        "  %s + écrivain N=%d  BLOCKED=%d CHANGE=%d écrasés=%d autres=%d raisons=%s"
        % (kind, ROUNDS, blocked, change, crushed, other, reasons)
    )
    if crushed:
        return 1
    if blocked == 0 and change:
        print("  *** ÉCART %s : écrivain actif, 0 BLOCKED workspace_contention ***" % kind)
        return 1
    return 0


if __name__ == "__main__":
    failures = 0
    if not positive_ingest():
        failures += 1
    if not positive_record():
        failures += 1
    if rounds("ingest"):
        failures += 1
    if rounds("record"):
        failures += 1
    finir(failures)
