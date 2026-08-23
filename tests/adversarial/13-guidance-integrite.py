import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, verifier_arbre_importe, finir
verifier_arbre_importe()

"""Integration guidance : agregat et atomicite, mode du fichier, course lecture/ecriture."""
import hashlib, json, os, stat, subprocess, sys, tempfile, shutil, threading, time
from pathlib import Path

failures = 0


def cli(ws, *a, timeout=120):
    r = subprocess.run([AEF, "--json", "--workspace", str(ws), *a],
                       capture_output=True, text=True, timeout=timeout)
    try:
        e = json.loads(r.stdout)
    except Exception:
        e = {}
    return r.returncode, e.get("status"), e

def init(ws):
    cli(ws, "init", "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z")

def new(pfx="e6-"):
    ws = Path(tempfile.mkdtemp(prefix=pfx)); init(ws); return ws

print("=== marqueur AGENTS:END orphelin puis `integrate all`")
ws = new()
(ws / "AGENTS.md").write_text("# doc\n\n<!-- AEF:AGENTS:END -->\n\nprose\n", encoding="utf-8")
c, s, e = cli(ws, "integrate", "all")
res = e.get("result") or {}
doors = res.get("doors") or {}
print("  status=%s exit=%s ok=%s installed=%s bridge_healthy=%s" %
      (s, c, e.get("ok"), res.get("installed"), res.get("bridge_healthy")))
print("  portes :", {k: (v.get("status") if isinstance(v, dict) else v) for k, v in doors.items()})
print("  CLAUDE.md ecrit ? %s   GEMINI.md ecrit ? %s"
      % ((ws / "CLAUDE.md").exists(), (ws / "GEMINI.md").exists()))
verdict = "OK" if (s == "BLOCKED" and c != 0 and not (ws / "CLAUDE.md").exists()
                   and not (ws / "GEMINI.md").exists()) else "*** ECART ***"
print("  ->", verdict)
if verdict != "OK":
    failures += 1
shutil.rmtree(ws, ignore_errors=True)

print("=== CONTROLE POSITIF : aucune porte bloquee")
ws = new()
c, s, e = cli(ws, "integrate", "all")
res = e.get("result") or {}
print("  status=%s exit=%s bridge_healthy=%s  CLAUDE=%s GEMINI=%s AGENTS=%s"
      % (s, c, res.get("bridge_healthy"), (ws / "CLAUDE.md").exists(),
         (ws / "GEMINI.md").exists(), (ws / "AGENTS.md").exists()))
shutil.rmtree(ws, ignore_errors=True)

print("=== mode du fichier")
for mode, attendu in ((0o644, "preserve"), (0o600, "preserve"), (0o444, "refus")):
    ws = new()
    p = ws / "AGENTS.md"
    p.write_text("# prose utilisateur\n", encoding="utf-8")
    os.chmod(p, mode)
    avant = stat.S_IMODE(p.stat().st_mode)
    h0 = hashlib.sha256(p.read_bytes()).hexdigest()
    c, s, e = cli(ws, "integrate", "agents")
    apres = stat.S_IMODE(p.stat().st_mode)
    h1 = hashlib.sha256(p.read_bytes()).hexdigest()
    if attendu == "preserve":
        ok = "OK" if apres == avant else "*** MODE PERDU ***"
        print("  mode %04o -> %04o apres integrate (%s)  %s" % (avant, apres, s, ok))
        if ok != "OK":
            failures += 1
    else:
        ok = "OK" if (s != "CHANGE" and h1 == h0) else "*** FICHIER LECTURE SEULE REMPLACE ***"
        print("  mode %04o -> status=%s exit=%s, octets inchanges=%s  %s"
              % (avant, s, c, h1 == h0, ok))
        if ok != "OK":
            failures += 1
    shutil.rmtree(ws, ignore_errors=True)

print("=== course entre la lecture et l'ecriture")
ecrases = 0
essais = 120
for i in range(essais):
    ws = new("toc-")
    p = ws / "AGENTS.md"
    p.write_text("# base\n", encoding="utf-8")
    sentinelle = "EDITION-UTILISATEUR-%d\n" % i
    stop = threading.Event()

    def writer():
        # ecrit la sentinelle en boucle serree pendant l'integration
        while not stop.is_set():
            try:
                p.write_text("# base\n" + sentinelle, encoding="utf-8")
            except OSError:
                pass
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    cli(ws, "integrate", "agents")
    stop.set(); t.join(timeout=2)
    try:
        final = p.read_text(encoding="utf-8")
    except OSError:
        final = ""
    # si le fichier contient le segment AEF mais PAS la sentinelle, l'edition a ete ecrasee
    if "AEF:AGENTS:BEGIN" in final and sentinelle.strip() not in final:
        ecrases += 1
    shutil.rmtree(ws, ignore_errors=True)
print("  editions utilisateur ecrasees : %d / %d (%.1f %%)" % (ecrases, essais, 100.0 * ecrases / essais))
if ecrases:
    failures += 1

print("=== mineur : enveloppe EVALUATE alignee sur UPGRADE ?")
for nom, chemin in (("EVALUATE", ".agent/state/evaluation-transaction.json"),
                    ("UPGRADE", ".agent/state/upgrade-transaction.json")):
    ws = new("env-")
    (ws / chemin).write_text(json.dumps({"phase": "apply", "paths": {}}), encoding="utf-8")
    rec = {"protocol": "aef.record.submit/v1", "record_id": "session-alpha",
           "recorded_at": "2026-08-20T13:21:00Z",
           "declared_by": {"kind": "human", "identifier": "operator"},
           "payload": {"context": "c", "actions": [{"summary": "s"}],
                       "outcomes": [], "incidents": [], "evidence": []}}
    (ws / "r.json").write_text(json.dumps(rec), encoding="utf-8")
    c, s, e = cli(ws, "record", "--recording", str(ws / "r.json"))
    print("  %-8s -> status=%-8s exit=%s meta=%s error=%s"
          % (nom, s, c, e.get("meta"), (e.get("error") or {}).get("code")))
    shutil.rmtree(ws, ignore_errors=True)

print("=== mineur : segment prive de son newline final")
ws = new("nl-")
cli(ws, "integrate", "agents")
p = ws / "AGENTS.md"
txt = p.read_text(encoding="utf-8")
p.write_text(txt.rstrip("\n"), encoding="utf-8")
c, s, e = cli(ws, "integrate", "agents", "--status")
print("  status apres retrait du \\n final : %s installed=%s"
      % (s, (e.get("result") or {}).get("installed")))
c, s, e = cli(ws, "integrate", "agents", "--remove")
print("  --remove -> %s exit=%s   (une sortie doit exister)" % (s, c))
if (e.get("result") or {}).get("installed") is True and s == "ERROR":
    failures += 1
shutil.rmtree(ws, ignore_errors=True)
finir(failures)
