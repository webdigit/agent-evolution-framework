import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()


import json, subprocess, sys, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(ROOT / "src"))
from aef.record_document import build_persisted_record
AEF=AEF
def cli(ws,*a,timeout=120):
    r=subprocess.run([*AEF,"--json","--workspace",str(ws),*a],capture_output=True,text=True,timeout=timeout)
    try:e=json.loads(r.stdout)
    except Exception:e={}
    return r.returncode,e.get("status"),(e.get("meta") or {}).get("reason"),r.stderr[:200],e.get("error")
def sub():
    return {"protocol":"aef.record.submit/v1","record_id":"session-alpha","recorded_at":"2026-08-20T13:21:00Z",
      "declared_by":{"kind":"human","identifier":"operator"},
      "payload":{"context":"reviewed a failed dry-run","actions":[{"summary":"inspected the CLI envelope"}],
                 "outcomes":[],"incidents":[],"evidence":[]}}
def decl(p,cid):
    return {"protocol":"aef.competency.declare.submit/v1","competency_id":cid,"title":"T","scope":"S",
      "limits":"L","rationale":"R","records":[{"record_id":p["record_id"],"digest":p["digest"]}],
      "decision":{"source":"human","actor":"operator","decided_at":"2026-08-21T10:00:00Z","approved":True}}
def count(ws):
    f=ws/".agent/state/competencies.json"
    return len([k for k in json.loads(f.read_text())if k.startswith("comp-")]) if f.exists() else -1
bad=0; errs=[]
ROUNDS=6; N=8
for k in range(ROUNDS):
    ws=Path(tempfile.mkdtemp(prefix=f"r{k}-"))
    cli(ws,"init","--instance-id","agent-1","--role","operator","--created-at","2026-08-20T13:21:00Z")
    d=sub(); (ws/"r.json").write_text(json.dumps(d)); cli(ws,"record","--recording",str(ws/"r.json"))
    p=build_persisted_record(d); paths=[]
    for i in range(N):
        f=ws/f"d{i}.json"; f.write_text(json.dumps(decl(p,f"comp-{i:03d}"))); paths.append(f)
    with ThreadPoolExecutor(max_workers=N) as pool:
        res=list(pool.map(lambda f: cli(ws,"competency","declare","--declaration",str(f)), paths))
    ch=sum(1 for c,s,_,_,_ in res if c==0 and s=="CHANGE"); pers=count(ws)
    codes=sorted({c for c,_,_,_,_ in res})
    for c,s,r,se,er in res:
        if s not in {"CHANGE","BLOCKED"}: errs.append((c,s,er,se))
    flag = "OK" if ch==pers else "ÉCART"
    if ch!=pers: bad+=1
    print(f"  tour {k+1}: CHANGE={ch} persistées={pers} codes={codes}  {flag}")
    shutil.rmtree(ws,ignore_errors=True)
print(f"  => {bad}/{ROUNDS} tours avec succès fantôme")
for e in errs[:4]: print("  ERREUR:", e[0], e[1], repr(e[2])[:160], repr(e[3])[:160])
finir(bad + (1 if errs else 0))
