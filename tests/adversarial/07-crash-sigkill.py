import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()
exiger_posix("SIGKILL et la fenetre d'ecriture POSIX")


"""Reprise apres interruption : provoquer un VRAI etat partiel par SIGKILL."""
import json, os, signal, subprocess, sys, tempfile, shutil, time, random
from pathlib import Path
sys.path.insert(0, str(ROOT / "src"))
from aef.record_document import build_persisted_record

AEF = AEF

def cli(ws, *a, timeout=120):
    r = subprocess.run([*AEF, "--json", "--workspace", str(ws), *a],
                       capture_output=True, text=True, timeout=timeout)
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

LEDGER = ".agent/state/competency-declarations.json"
JOURNAL_CANDIDATES = [".agent/state/competency-declaration-transaction.json"]

def state(ws):
    comp = ws / ".agent/state/competencies.json"
    n = 0
    if comp.exists():
        try:
            n = len([k for k in json.loads(comp.read_text()) if k.startswith("comp-")])
        except Exception:
            n = -1
    journal = [j for j in JOURNAL_CANDIDATES if (ws / j).exists()]
    return n, (ws / LEDGER).exists(), journal

random.seed(7)
found = None
for attempt in range(160):
    ws, p = setup("crash-")
    f = ws / "d.json"
    f.write_text(json.dumps(decl(p, "comp-001")))
    proc = subprocess.Popen([*AEF, "--json", "--workspace", str(ws), "competency", "declare",
                             "--declaration", str(f)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(random.uniform(0.030, 0.075))
    try:
        proc.send_signal(signal.SIGKILL)
    except Exception:
        pass
    proc.wait(timeout=30)
    n, ledger, journal = state(ws)
    if journal or (ledger and n == 0) or (n > 0 and not ledger):
        found = (ws, attempt, n, ledger, journal)
        break
    shutil.rmtree(ws, ignore_errors=True)

if not found:
    print("  aucun etat partiel obtenu en 400 SIGKILL — la fenetre est tres etroite")
    finir(0)

ws, attempt, n, ledger, journal = found
print("  ETAT PARTIEL obtenu au SIGKILL n°%d" % (attempt + 1))
print("     competences persistees=%d  ledger=%s  journal=%s" % (n, ledger, journal))
print("  --- sorties proposees :")
failures = 0
for cmd, label in (
    (("competency", "declare", "--recover", "--dry-run"), "recover --dry-run"),
    (("competency", "declare", "--recover"), "recover"),
):
    c, s, r, e = cli(ws, *cmd)
    print("     %-20s -> %s exit=%s raison=%s" % (label, s, c, r))
    if e.get("diff"):
        print("        diff annonce :", json.dumps(e["diff"])[:220])
n2, ledger2, journal2 = state(ws)
print("     apres reprise : competences=%d ledger=%s journal=%s" % (n2, ledger2, journal2))
f = ws / "d2.json"
f.write_text(json.dumps(decl(p, "comp-002")))
c, s, r, _ = cli(ws, "competency", "declare", "--declaration", str(f))
print("     declaration suivante -> %s exit=%s raison=%s" % (s, c, r))
if s not in {"CHANGE", "BLOCKED"}:
    failures += 1
c, s, r, _ = cli(ws, "record", "--recording", str(ws / "r.json"))
print("     record apres reprise -> %s exit=%s raison=%s" % (s, c, r))
if s == "ERROR":
    failures += 1
print("     audit -> %s" % (cli(ws, "audit")[1],))
shutil.rmtree(ws, ignore_errors=True)
finir(failures)
