import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()


"""Rejeu externe de la concurrence INGEST — tout via la CLI, aucun helper pytest."""
import json, os, subprocess, sys, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(ROOT / "src"))
from aef.record_document import build_persisted_record

AEF = AEF
def cli(ws, *args, timeout=120):
    r = subprocess.run([*AEF, "--json", "--workspace", str(ws), *args],
                       capture_output=True, text=True, timeout=timeout)
    try: e = json.loads(r.stdout)
    except Exception: e = {}
    return r.returncode, e.get("status"), (e.get("meta") or {}).get("reason"), e

def submission(**ov):
    d = {"protocol":"aef.record.submit/v1","record_id":"session-alpha","recorded_at":"2026-08-20T13:21:00Z",
         "declared_by":{"kind":"human","identifier":"operator"},
         "payload":{"context":"reviewed a failed dry-run","actions":[{"summary":"inspected the CLI envelope"}],
                    "outcomes":[],"incidents":[],"evidence":[]}}
    d.update(ov); return d

def setup(prefix):
    ws = Path(tempfile.mkdtemp(prefix=prefix))
    c,s,_,_ = cli(ws, "init", "--instance-id","agent-1","--role","operator","--created-at","2026-08-20T13:21:00Z")
    assert s in {"CHANGE","NO_CHANGE"}, s
    doc = submission()
    (ws/"recording.json").write_text(json.dumps(doc))
    c,s,_,_ = cli(ws, "record", "--recording", str(ws/"recording.json"))
    assert s == "CHANGE", s
    return ws, build_persisted_record(doc)

def intake_for(p, events): return {"protocol":"aef.ingest.submit/v1","records":[{"record_id":p["record_id"],"digest":p["digest"],"events":events}]}

def scenario(n):
    ws, p = setup(f"conc{n}-")
    paths=[]
    for i in range(n):
        f = ws/f"intake-{i}.json"
        f.write_text(json.dumps(intake_for(p, [{"id":f"evt-{i:03d}","novel":True,"pattern_key":f"pattern-{i:03d}"}])))
        paths.append(f)
    with ThreadPoolExecutor(max_workers=n) as pool:
        res = list(pool.map(lambda f: cli(ws, "ingest", "--intake", str(f)), paths))
    kn = json.loads((ws/".agent/knowledge/knowledge.json").read_text())
    signals = len(kn.get("signals") or [])
    change = sum(1 for c,s,_,_ in res if c==0 and s=="CHANGE")
    blocked = sum(1 for c,s,_,_ in res if s=="BLOCKED")
    reasons = sorted({r for _,s,r,_ in res if s=="BLOCKED" and r})
    other = [(c,s) for c,s,_,_ in res if s not in {"CHANGE","BLOCKED"}]
    held = change == signals
    ok = "OK" if held else "*** ÉCART ***"
    print(f"  N={n:3d}  CHANGE={change:3d}  BLOCKED={blocked:3d}  signaux={signals:3d}  {ok}  raisons={reasons} autres={other[:3]}")
    shutil.rmtree(ws, ignore_errors=True)
    return held, other

def sequential(n=8):
    ws, p = setup("seq-")
    ch=0
    for i in range(n):
        f = ws/f"i{i}.json"; f.write_text(json.dumps(intake_for(p,[{"id":f"evt-{i:03d}","novel":True,"pattern_key":f"pattern-{i:03d}"}])))
        c,s,_,_ = cli(ws,"ingest","--intake",str(f))
        if s=="CHANGE": ch+=1
    kn = json.loads((ws/".agent/knowledge/knowledge.json").read_text())
    signals = len(kn.get("signals") or [])
    print(f"  CONTRÔLE POSITIF séquentiel N={n} : CHANGE={ch} signaux={signals}")
    shutil.rmtree(ws, ignore_errors=True)
    return ch == signals == n

if __name__ == "__main__":
    failures = 0
    if not sequential(8):
        failures += 1
    for n in (8, 16, 32):
        held, other = scenario(n)
        if not held or other:
            failures += 1
    finir(failures)
