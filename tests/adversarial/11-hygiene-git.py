import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, verifier_arbre_importe, finir
verifier_arbre_importe()

"""Le verrou ne doit apparaitre ni dans git status apres init, ni dans l historique."""
import json, subprocess, tempfile, shutil
from pathlib import Path

def cli(ws, *a):
    return subprocess.run([*AEF, "--json", "--workspace", str(ws), *a],
                          capture_output=True, text=True, timeout=120)

T = Path(tempfile.mkdtemp())
ws = T / "ws"
ws.mkdir()
subprocess.run(["git", "init", "-q", str(ws)], check=True)
(ws / ".gitignore").write_text("# gitignore de l utilisateur\nnode_modules/\nsecrets.txt\n", encoding="utf-8")
cli(ws, "init", "--role", "operator")
gi = (ws / ".gitignore").read_text(encoding="utf-8")
print("--- contenu utilisateur preserve ?", "oui" if "secrets.txt" in gi else "NON — REGRESSION")
failures = 0 if "secrets.txt" in gi else 1
cli(ws, "init", "--role", "operator")
gi = (ws / ".gitignore").read_text(encoding="utf-8")
print("--- idempotence : bloc AEF present %d fois (attendu 1)" % gi.count("AEF runtime workspace locks"))
if gi.count("AEF runtime workspace locks") != 1:
    failures += 1
subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True)
st = subprocess.run(["git", "-C", str(ws), "status", "--short"], capture_output=True, text=True).stdout
staged_locks = sum(1 for l in st.splitlines() if "lock" in l.lower())
print("--- verrou stage apres init (attendu 0) :", staged_locks)
if staged_locks:
    failures += 1
rec = {"protocol": "aef.record.submit/v1", "record_id": "session-alpha",
       "recorded_at": "2026-08-20T13:21:00Z",
       "declared_by": {"kind": "human", "identifier": "operator"},
       "payload": {"context": "c", "actions": [{"summary": "s"}],
                   "outcomes": [], "incidents": [], "evidence": []}}
(T / "rec.json").write_text(json.dumps(rec), encoding="utf-8")
cli(ws, "record", "--recording", str(T / "rec.json"))
subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True)
files = subprocess.run(["git", "-C", str(ws), "ls-files"], capture_output=True, text=True).stdout
locks = [f for f in files.splitlines() if "lock" in f.lower()]
print("--- verrou suivi apres record (attendu aucun) :", locks or "(aucun)")
if locks:
    failures += 1
shutil.rmtree(T, ignore_errors=True)
finir(failures)
