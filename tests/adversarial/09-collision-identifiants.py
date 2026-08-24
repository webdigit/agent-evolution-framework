import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()


"""Gardes mutuelles croisees et collision d identifiant (casse x normalisation)."""
import json, subprocess, sys, tempfile, shutil, unicodedata
from pathlib import Path
sys.path.insert(0, str(ROOT / "src"))
from aef.record_document import build_persisted_record

AEF = AEF
EV = ".agent/state/evaluation-transaction.json"
UP = ".agent/state/upgrade-transaction.json"

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

def decl(p, cid):
    return {"protocol": "aef.competency.declare.submit/v1", "competency_id": cid,
            "title": "T", "scope": "S", "limits": "L", "rationale": "R",
            "records": [{"record_id": p["record_id"], "digest": p["digest"]}],
            "decision": {"source": "human", "actor": "operator",
                         "decided_at": "2026-08-21T10:00:00Z", "approved": True}}

def setup(pfx):
    ws = Path(tempfile.mkdtemp(prefix=pfx))
    cli(ws, "init", "--instance-id", "agent-1", "--role", "operator", "--created-at", "2026-08-20T13:21:00Z")
    d = sub()
    (ws / "r.json").write_text(json.dumps(d))
    cli(ws, "record", "--recording", str(ws / "r.json"))
    return ws, build_persisted_record(d)

print("=== gardes mutuelles croisees")
for name, path in (("journal EVALUATE", EV), ("journal UPGRADE", UP)):
    for cmd, label in ((("competency", "declare", "--recover"), "declare --recover"),
                       (("competency", "declare", "--recover", "--dry-run"), "declare --recover --dry-run"),
                       (("record", "--recording", "R"), "record")):
        ws, p = setup("g-")
        (ws / path).write_text(json.dumps({"phase": "apply", "paths": {}}))
        args = tuple(str(ws / "r.json") if x == "R" else x for x in cmd)
        c, s, r, _ = cli(ws, *args)
        print("  %-16s + %-28s -> %s exit=%s raison=%s" % (name, label, s, c, r))
        shutil.rmtree(ws, ignore_errors=True)

print("=== matrice casse x normalisation")
ws, p = setup("m3-")
base = "cafe" + "́"                      # cafe + combining acute (NFD)
variants = {
    "NFC minuscule": unicodedata.normalize("NFC", base),
    "NFD minuscule": unicodedata.normalize("NFD", base),
    "NFC majuscule": unicodedata.normalize("NFC", base).upper(),
    "NFD majuscule": unicodedata.normalize("NFD", base).upper(),
    "Capitalise NFC": unicodedata.normalize("NFC", base).capitalize(),
}
first = None
failures = 0
for label, cid in variants.items():
    f = ws / "d.json"
    f.write_text(json.dumps(decl(p, cid)))
    c, s, r, _ = cli(ws, "competency", "declare", "--declaration", str(f))
    if first is None:
        first = label
        print("  %-16s (%-10s) -> %s   [premiere declaration]" % (label, repr(cid)[:10], s))
        if s != "CHANGE":
            failures += 1
    else:
        verdict = "OK (collision detectee)" if s != "CHANGE" else "*** ACCEPTEE — PAS DE COLLISION ***"
        print("  %-16s (%-10s) -> %s raison=%s  %s" % (label, repr(cid)[:10], s, r, verdict))
        if s == "CHANGE":
            failures += 1
comp = json.loads((ws / ".agent/state/competencies.json").read_text())
print("  competences finalement persistees :", [k for k in comp if "caf" in k.lower() or "CAF" in k])
shutil.rmtree(ws, ignore_errors=True)
finir(failures)
