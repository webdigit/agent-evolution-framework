import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, PY, exiger_posix, verifier_arbre_importe, finir
verifier_arbre_importe()
exiger_posix("les temoins utilisent mkfifo, les symlinks et chmod 000")


import json, os, subprocess, sys, tempfile, shutil
from pathlib import Path
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
    return r.returncode, e.get("status"), (e.get("meta") or {}).get("reason"), e

def sub(rid="session-alpha"):
    return {"protocol": "aef.record.submit/v1", "record_id": rid,
            "recorded_at": "2026-08-20T13:21:00Z",
            "declared_by": {"kind": "human", "identifier": "operator"},
            "payload": {"context": "c", "actions": [{"summary": "s"}],
                        "outcomes": [], "incidents": [], "evidence": []}}

def intake(p, events):
    return {"protocol": "aef.ingest.submit/v1",
            "records": [{"record_id": p["record_id"], "digest": p["digest"], "events": events}]}

def setup(pfx):
    ws = Path(tempfile.mkdtemp(prefix=pfx))
    cli(ws, "init", "--instance-id", "agent-1", "--role", "operator", "--created-at", "2026-08-20T13:21:00Z")
    d = sub()
    (ws / "r.json").write_text(json.dumps(d))
    cli(ws, "record", "--recording", str(ws / "r.json"))
    return ws, build_persisted_record(d)

print("=== code de sortie pour un identifiant refuse")
failures = 0
ws, p = setup("x-")
f = ws / "h.json"
f.write_text(json.dumps(intake(p, [{"id": "ABC", "novel": True, "pattern_key": "pk-ok"}])))
c, s, r, e = cli(ws, "ingest", "--intake", str(f))
print("  event.id 'ABC' -> status=%s exit=%s error=%s" % (s, c, json.dumps(e.get("error"))[:200]))
if s == "CHANGE":
    failures += 1
f.write_text(json.dumps(intake(p, [{"id": "ok1", "novel": True, "pattern_key": "pk-ok"}])))
ok_c, ok_s, ok_r, _ = cli(ws, "ingest", "--intake", str(f))
print("  temoin valide  -> status=%s exit=%s" % (ok_s, ok_c))
if ok_s != "CHANGE":
    failures += 1
shutil.rmtree(ws, ignore_errors=True)

print("=== dry-run vs apply pour plusieurs etats du journal EVALUATE")
JOURNAL = ".agent/state/evaluation-transaction.json"
def variant(kind):
    ws, p = setup("m2-")
    j = ws / JOURNAL
    j.parent.mkdir(parents=True, exist_ok=True)
    if kind == "directory":
        j.mkdir()
    elif kind == "broken_symlink":
        j.symlink_to(ws / "nowhere-at-all")
    elif kind == "fifo":
        os.mkfifo(j)
    elif kind == "empty":
        j.write_text("")
    elif kind == "invalid_json":
        j.write_text("{not json")
    elif kind == "unreadable":
        j.write_text("{}")
        os.chmod(j, 0o000)
    elif kind == "symlink_outside":
        target = Path(tempfile.mkdtemp()) / "out.json"
        target.write_text("{}")
        j.symlink_to(target)
    f = ws / "i.json"
    f.write_text(json.dumps(intake(p, [{"id": "e1", "novel": True, "pattern_key": "pk-one"}])))
    cd, sd, rd, _ = cli(ws, "ingest", "--intake", str(f), "--dry-run")
    ca, sa, ra, _ = cli(ws, "ingest", "--intake", str(f))
    same = (cd == ca) and (sd == sa) and (rd == ra)
    print("  %-16s dry-run=%s/%s/%s  apply=%s/%s/%s  %s"
          % (kind, sd, cd, rd, sa, ca, ra, "OK" if same else "*** DIVERGENCE ***"))
    try:
        os.chmod(ws / JOURNAL, 0o644)
    except Exception:
        pass
    shutil.rmtree(ws, ignore_errors=True)
    return same

for k in ("directory", "broken_symlink", "fifo", "empty", "invalid_json", "unreadable", "symlink_outside"):
    try:
        if not variant(k):
            failures += 1
    except Exception as ex:
        print("  %-16s ERREUR harness: %s" % (k, ex))
        failures += 1
finir(failures)
