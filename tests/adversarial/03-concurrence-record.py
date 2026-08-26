import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()


import json, subprocess, sys, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(ROOT / "src"))
AEF=AEF
def cli(ws,*a,timeout=120):
    r=subprocess.run([*AEF,"--json","--workspace",str(ws),*a],capture_output=True,text=True,timeout=timeout)
    try:e=json.loads(r.stdout)
    except Exception:e={}
    return r.returncode,e.get("status"),(e.get("meta") or {}).get("reason")
def sub(rid):
    return {"protocol":"aef.record.submit/v1","record_id":rid,"recorded_at":"2026-08-20T13:21:00Z",
      "declared_by":{"kind":"human","identifier":"operator"},
      "payload":{"context":"c","actions":[{"summary":"s"}],"outcomes":[],"incidents":[],"evidence":[]}}
ws=Path(tempfile.mkdtemp(prefix="rec-"))
cli(ws,"init","--instance-id","agent-1","--role","operator","--created-at","2026-08-20T13:21:00Z")
N=16; paths=[]
for i in range(N):
    f=ws/f"r{i}.json"; f.write_text(json.dumps(sub(f"session-{i:03d}"))); paths.append(f)
with ThreadPoolExecutor(max_workers=N) as p:
    res=list(p.map(lambda f: cli(ws,"record","--recording",str(f)), paths))
recs=list((ws/".agent/records").glob("*.json")) if (ws/".agent/records").is_dir() else []
idx=json.loads((ws/".agent/knowledge/knowledge.json").read_text())
change=sum(1 for c,s,_ in res if c==0 and s=="CHANGE")
blocked=sum(1 for c,s,_ in res if s=="BLOCKED")
reasons=sorted({r for _,s,r in res if s=="BLOCKED" and r})
print(f"  RECORD concurrent N={N} : CHANGE={change} BLOCKED={blocked} fichiers records={len(recs)} raisons={reasons}")
print(f"  {'OK' if change==len(recs) else '*** ÉCART : ' + str(change) + ' succès annoncés pour ' + str(len(recs)) + ' persistés ***'}")
shutil.rmtree(ws,ignore_errors=True)
finir(0 if change == len(recs) else 1)
