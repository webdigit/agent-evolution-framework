import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()

import json, subprocess, sys, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
N = int(sys.argv[2]) if len(sys.argv) > 2 else 8
BUILD = ROOT.name
sys.path.insert(0, str(ROOT / "src"))
from aef.record_document import build_persisted_record

AEF = AEF

def cli(ws, *a):
    r = subprocess.run([*AEF, "--json", "--workspace", str(ws), *a],
                       capture_output=True, text=True, timeout=120)
    try:
        e = json.loads(r.stdout)
    except Exception:
        e = {}
    return r.returncode, e.get("status"), (e.get("meta") or {}).get("reason"), (e.get("error") or {}).get("code")

def sub():
    return {"protocol": "aef.record.submit/v1", "record_id": "session-alpha",
            "recorded_at": "2026-08-20T13:21:00Z",
            "declared_by": {"kind": "human", "identifier": "operator"},
            "payload": {"context": "c", "actions": [{"summary": "s"}],
                        "outcomes": [], "incidents": [], "evidence": []}}

def intake(p, ev):
    return {"protocol": "aef.ingest.submit/v1",
            "records": [{"record_id": p["record_id"], "digest": p["digest"], "events": ev}]}

tot_err = 0
tot_runs = 0
for k in range(ROUNDS):
    ws = Path(tempfile.mkdtemp(prefix="reg-r%d-" % k))
    cli(ws, "init", "--instance-id", "agent-1", "--role", "operator", "--created-at", "2026-08-20T13:21:00Z")
    d = sub()
    (ws / "r.json").write_text(json.dumps(d))
    cli(ws, "record", "--recording", str(ws / "r.json"))
    p = build_persisted_record(d)
    paths = []
    for i in range(N):
        f = ws / f"i{i}.json"
        f.write_text(json.dumps(intake(p, [{"id": "e%03d" % i, "novel": True, "pattern_key": "p%03d" % i}])))
        paths.append(f)
    with ThreadPoolExecutor(max_workers=N) as pool:
        res = list(pool.map(lambda f: cli(ws, "ingest", "--intake", str(f)), paths))
    kn = json.loads((ws / ".agent/knowledge/knowledge.json").read_text())
    sig = len(kn.get("signals") or [])
    ch = sum(1 for c, s, _, _ in res if c == 0 and s == "CHANGE")
    errs = [(c, s, e) for c, s, _, e in res if s not in {"CHANGE", "BLOCKED"}]
    tot_err += len(errs)
    tot_runs += N
    if ch != sig:
        tot_err += 1
    print("  %-6s tour %d : CHANGE=%d signaux=%d  ERREURS=%d %s  %s"
          % (BUILD.split("/")[-1], k + 1, ch, sig, len(errs), errs[:2], "OK" if ch == sig else "*** ECART ***"))
    shutil.rmtree(ws, ignore_errors=True)
print("  %-6s TOTAL : %d erreurs sur %d exécutions concurrentes (%.1f%%)"
      % (BUILD.split("/")[-1], tot_err, tot_runs, 100.0 * tot_err / tot_runs))
finir(0 if tot_err == 0 else 1)
