import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from bancenv import ROOT, AEF, verifier_arbre_importe, finir
verifier_arbre_importe()

"""Un marqueur situe dans une fence markdown n'est pas un marqueur.
Compare les OCTETS (sha256) avant/apres, pas seulement le statut."""
import hashlib, json, os, subprocess, sys, tempfile, shutil
from pathlib import Path


DOORS = {
    "agents":  ("AGENTS.md", "AEF:AGENTS"),
    "claude":  ("CLAUDE.md", "AEF:CLAUDE-ROOT"),
    "gemini":  ("GEMINI.md", "AEF:GEMINI"),
}

def cli(ws, *a):
    r = subprocess.run([*AEF, "--json", "--workspace", str(ws), *a],
                       capture_output=True, text=True, timeout=120)
    try:
        e = json.loads(r.stdout)
    except Exception:
        e = {}
    return r.returncode, e.get("status"), e

def init(ws):
    cli(ws, "init", "--instance-id", "agent-1", "--role", "operator",
        "--created-at", "2026-08-20T13:21:00Z")

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:12] if Path(p).exists() else "ABSENT"

def doc(marker, style, indent=0, info="", closed=True):
    """Un fichier qui DOCUMENTE le bloc AEF dans une fence."""
    pad = " " * indent
    body = [
        "# Mon projet",
        "",
        "Voici a quoi ressemble le bloc AEF, pour information :",
        "",
    ]
    if style == "indent4":
        body += [
            "    <!-- %s:BEGIN version=\"1.0.0\" -->" % marker,
            "    contenu documente par l utilisateur",
            "    <!-- %s:END -->" % marker,
        ]
    else:
        opener = pad + style + info
        body += [opener,
                 "<!-- %s:BEGIN version=\"1.0.0\" -->" % marker,
                 "contenu documente par l utilisateur",
                 "<!-- %s:END -->" % marker]
        if closed:
            body.append(pad + style)
    body += ["", "Fin de ma prose personnelle.", ""]
    return "\n".join(body)

CASES = [
    ("fence ```",            "```",  0, ""),
    ("fence ```markdown",    "```",  0, "markdown"),
    ("fence ~~~",            "~~~",  0, ""),
    ("fence indentee 3sp",   "```",  3, ""),
    ("bloc indente 4sp",     "indent4", 0, ""),
]

print("=== marqueur cite dans une fence : 3 portes x 5 formes x 3 modes")
echecs = 0
for door, (fname, marker) in DOORS.items():
    for label, style, indent, info in CASES:
        for closed in (True, False):
            if style == "indent4" and not closed:
                continue
            ws = Path(tempfile.mkdtemp(prefix="fence-"))
            init(ws)
            target = ws / fname
            original = doc(marker, style, indent, info, closed)
            target.write_text(original, encoding="utf-8")
            h0 = sha(target)
            suffix = "" if closed else " (non fermee)"

            # 1) --status ne doit pas dire « installe »
            c, s, e = cli(ws, "integrate", door, "--status")
            res = e.get("result") or {}
            installed = res.get("installed")
            if installed is True:
                print("  [%-7s] %-22s%-14s STATUS dit installed=True  *** FAUSSE INSTALLATION ***"
                      % (door, label, suffix))
                echecs += 1

            # 2) --remove ne doit toucher aucun octet
            c, s, e = cli(ws, "integrate", door, "--remove")
            h1 = sha(target)
            if h1 != h0:
                after = target.read_text(encoding="utf-8")
                perdu = len(original) - len(after)
                print("  [%-7s] %-22s%-14s REMOVE a modifie le fichier (%+d octets)  *** PROSE TOUCHEE ***"
                      % (door, label, suffix, -perdu))
                echecs += 1

            # 3) install doit poser le vrai segment HORS fence et preserver la prose citee
            target.write_text(original, encoding="utf-8")
            c, s, e = cli(ws, "integrate", door)
            after = target.read_text(encoding="utf-8") if target.exists() else ""
            if "contenu documente par l utilisateur" not in after:
                print("  [%-7s] %-22s%-14s INSTALL a supprime la prose citee  *** ***"
                      % (door, label, suffix))
                echecs += 1
            if "Fin de ma prose personnelle." not in after:
                print("  [%-7s] %-22s%-14s INSTALL a supprime la prose finale  *** ***"
                      % (door, label, suffix))
                echecs += 1
            shutil.rmtree(ws, ignore_errors=True)

print("  -> %d anomalie(s)" % echecs)

print("=== CONTROLE POSITIF : un VRAI marqueur hors fence doit etre vu")
for door, (fname, marker) in DOORS.items():
    ws = Path(tempfile.mkdtemp(prefix="ctl-"))
    init(ws)
    c, s, e = cli(ws, "integrate", door)
    inst_apres = ((cli(ws, "integrate", door, "--status")[2].get("result") or {}).get("installed"))
    c2, s2, e2 = cli(ws, "integrate", door, "--remove")
    inst_final = ((cli(ws, "integrate", door, "--status")[2].get("result") or {}).get("installed"))
    print("  [%-7s] install=%s -> status installed=%s ; remove=%s -> installed=%s  %s"
          % (door, s, inst_apres, s2, inst_final,
             "OK" if (inst_apres is True and inst_final is not True) else "*** LE SCENARIO NE DISCRIMINE PAS ***"))
    if not (inst_apres is True and inst_final is not True):
        echecs += 1
    shutil.rmtree(ws, ignore_errors=True)
finir(echecs)
