import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()


import json, subprocess, sys, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(ROOT / "src"))
from aef.record_document import build_persisted_record

AEF = AEF

def cli(ws, *a):
    r = subprocess.run([AEF, "--json", "--workspace", str(ws), *a],
                       capture_output=True, text=True, timeout=120)
    try:
        e = json.loads(r.stdout)
    except Exception:
        e = {}
    return r.returncode, e.get("status"), (e.get("meta") or {}).get("reason"), e

def sub():
    return {"protocol": "aef.record.submit/v1", "record_id": "session-alpha",
            "recorded_at": "2026-08-20T13:21:00Z",
            "declared_by": {"kind": "human", "identifier": "operator"},
            "payload": {"context": "c", "actions": [{"summary": "s"}],
                        "outcomes": [], "incidents": [], "evidence": []}}

def intake(p, ev):
    return {"protocol": "aef.ingest.submit/v1",
            "records": [{"record_id": p["record_id"], "digest": p["digest"], "events": ev}]}

ws = Path(tempfile.mkdtemp(prefix="cap-"))
cli(ws, "init", "--instance-id", "agent-1", "--role", "operator", "--created-at", "2026-08-20T13:21:00Z")
d = sub()
(ws / "r.json").write_text(json.dumps(d))
cli(ws, "record", "--recording", str(ws / "r.json"))
p = build_persisted_record(d)

def ev_count():
    kn = json.loads((ws / ".agent/knowledge/knowledge.json").read_text())
    for s in kn.get("signals", []):
        if "cap-test" in json.dumps(s):
            return len(s.get("evidence_ids") or [])
    return 0

first_block = None
for i in range(140):
    f = ws / "i.json"
    f.write_text(json.dumps(intake(p, [{"id": "c%04d" % i, "novel": True, "pattern_key": "cap-test"}])))
    c, s, r, e = cli(ws, "ingest", "--intake", str(f))
    if s != "CHANGE":
        print("  premier non-CHANGE a l'evenement #%d : status=%s exit=%s raison=%s" % (i, s, c, r))
        print("  result :", json.dumps(e.get("result"))[:300])
        print("  evidence_ids persistes :", ev_count())
        first_block = i
        block_status, block_exit, block_reason = s, c, r
        break
failures = 0
other_status = None
if first_block is None:
    print("  aucun blocage en 140 evenements ; evidence_ids =", ev_count())
    failures += 1
else:
    extra_held = True
    for extra in range(3):
        f = ws / "i.json"
        f.write_text(json.dumps(intake(p, [{"id": "z%04d" % extra, "novel": True, "pattern_key": "cap-test"}])))
        c, s, r, _ = cli(ws, "ingest", "--intake", str(f))
        print("  tentative suivante %d : %s exit=%s raison=%s  evidences=%d" % (extra + 1, s, c, r, ev_count()))
        if s != "BLOCKED" or r != "evidence_cap_exceeded" or c != 4:
            extra_held = False
    print("  audit final :", cli(ws, "audit")[1])
    f = ws / "j.json"
    f.write_text(json.dumps(intake(p, [{"id": "other1", "novel": True, "pattern_key": "autre-motif"}])))
    c, s, r, _ = cli(ws, "ingest", "--intake", str(f))
    other_status = s
    print("  autre pattern_key apres saturation :", s, "exit=%s" % c, r)
    if (
        not extra_held
        or s != "CHANGE"
        or block_status != "BLOCKED"
        or block_reason != "evidence_cap_exceeded"
        or block_exit != 4
    ):
        failures += 1
shutil.rmtree(ws, ignore_errors=True)
finir(failures)
