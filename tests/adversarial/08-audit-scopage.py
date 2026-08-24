import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()


"""Scopage de l audit : les cas LEGITIMES restent PASS, les cas fautifs rougissent."""
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
    return r.returncode, e.get("status"), e

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

COMP = ".agent/state/competencies.json"
LEDGER = ".agent/state/competency-declarations.json"

def findings(ws):
    c, s, e = cli(ws, "audit")
    f = (e.get("result") or {}).get("findings") or []
    return s, c, [(x.get("finding_id") or x.get("id") or x.get("code") or list(x)[:2]) if isinstance(x, dict) else x for x in f]

def show(label, ws, attendu):
    global failures
    s, c, f = findings(ws)
    ok = "OK" if ((attendu == "PASS") == (s == "PASS")) else "*** ECART ***"
    print("  %-52s -> %s exit=%s findings=%s  %s" % (label, s, c, f[:4], ok))
    if ok != "OK":
        failures += 1

failures = 0

# --- (a) BROWNFIELD : competences sans source "declared", aucun ledger
ws, p = setup("bf-")
comp = json.loads((ws / COMP).read_text())
comp["legacy-skill"] = {"level": "L3", "title": "Ancienne competence", "xp": 120}
(ws / COMP).write_text(json.dumps(comp, indent=2))
show("(a) brownfield : L3 sans source ni ledger  [attendu PASS]", ws, "PASS")
shutil.rmtree(ws, ignore_errors=True)

# --- (b) DECLAREE promue avec historique -> pas de finding
ws, p = setup("promu-")
f = ws / "d.json"; f.write_text(json.dumps(decl(p, "comp-promue")))
cli(ws, "competency", "declare", "--declaration", str(f))
comp = json.loads((ws / COMP).read_text())
comp["comp-promue"]["level"] = "L3"
(ws / COMP).write_text(json.dumps(comp, indent=2))
ev_path = ws / ".agent/state/evaluations.json"
ev = json.loads(ev_path.read_text()) if ev_path.exists() else {}
for key in ("promotion_decisions", "promotion_recommendations", "history"):
    ev.setdefault(key, [])
ev["promotion_recommendations"].append({"id": "rec-1", "competency_id": "comp-promue",
                                        "to_level": "L3", "status": "approved"})
ev["promotion_decisions"].append({"recommendation_id": "rec-1", "to_level": "L3",
                                  "decided_at": "2026-08-22T10:00:00Z", "approved": True})
ev_path.write_text(json.dumps(ev, indent=2))
show("(b) declaree L3 AVEC historique de promotion  [attendu PASS]", ws, "PASS")
shutil.rmtree(ws, ignore_errors=True)

# --- (c) FAUTIF 1 : declaree, ledger supprime
ws, p = setup("f1-")
f = ws / "d.json"; f.write_text(json.dumps(decl(p, "comp-x")))
cli(ws, "competency", "declare", "--declaration", str(f))
(ws / LEDGER).unlink(missing_ok=True)
show("(c) declaree, ledger supprime  [attendu ROUGE]", ws, "FAIL")
shutil.rmtree(ws, ignore_errors=True)

# --- (d) FAUTIF 2 : declaree promue L3 SANS historique
ws, p = setup("f2-")
f = ws / "d.json"; f.write_text(json.dumps(decl(p, "comp-y")))
cli(ws, "competency", "declare", "--declaration", str(f))
comp = json.loads((ws / COMP).read_text())
comp["comp-y"]["level"] = "L3"
(ws / COMP).write_text(json.dumps(comp, indent=2))
show("(d) declaree L3 SANS historique  [attendu ROUGE]", ws, "FAIL")
shutil.rmtree(ws, ignore_errors=True)

# --- (e) FAUTIF 3 : competence ajoutee a la main avec source declared, sans evenement
ws, p = setup("f3-")
comp = json.loads((ws / COMP).read_text())
comp["comp-fake"] = {"level": "L4", "title": "Fabriquee", "xp": 9999, "source": "declared"}
(ws / COMP).write_text(json.dumps(comp, indent=2))
show("(e) source=declared fabriquee L4/xp9999  [attendu ROUGE]", ws, "FAIL")
shutil.rmtree(ws, ignore_errors=True)
finir(failures)
