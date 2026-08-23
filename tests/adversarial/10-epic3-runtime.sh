#!/usr/bin/env bash
# Epic 3: zip-bomb wheel, repo binary exec, network. Exit 0 iff properties hold.
set -euo pipefail
: "${AEF_BUILD:?set AEF_BUILD to the tree under test}"
AEF="$AEF_BUILD/.venv/bin/aef"
HERE="$(cd "$(dirname "$0")" && pwd)"
WS="$(mktemp -d)"; trap 'rm -rf "$WS"' EXIT
mkdir -p "$WS/.agent" "$WS/dist" "$WS/.aef-venv/bin"
printf '{"expected_package_version": "1.2.0"}\n' > "$WS/.agent/runtime-requirements.json"
printf '{"schema_version": 1}\n' > "$WS/.agent/manifest.json"
printf 'home = /usr\nversion = 3.11.0\n' > "$WS/.aef-venv/pyvenv.cfg"

fail=0
python3 "$HERE/fabrique-roue-piegee.py" "$WS/dist/jsonschema-4.0.0-py3-none-any.whl"
echo "--- roue piegee (attendu : enveloppe rendue, RSS de l'ordre de 25 Mio, moins d'une seconde)"
set +e
/usr/bin/env python3 - "$AEF" "$WS" <<'PY'
import resource, subprocess, sys, time
aef, ws = sys.argv[1], sys.argv[2]
t0 = time.time()
p = subprocess.run([aef, "--workspace", ws, "--json", "doctor"], capture_output=True, text=True, timeout=300)
rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0
wall = time.time() - t0
print("    exit=%s  wall=%.2fs  RSS max=%.1f MiB" % (p.returncode, wall, rss))
# Property: the archive is not opened (RSS stays modest, wall stays short).
if wall > 30 or rss > 512:
    sys.exit(11)
sys.exit(0)
PY
rss_rc=$?
set -e
if [ "$rss_rc" -ne 0 ]; then
  echo "    *** RSS/wall hors borne — amplification possible ***"
  fail=1
fi

echo "--- binaire piege + reseau (attendu : execve=1, socket=0, connect=0, temoin absent)"
printf '#!/bin/sh\ntouch %s/PWNED\n' "$WS" > "$WS/.aef-venv/bin/python3"; chmod +x "$WS/.aef-venv/bin/python3"
if ! command -v strace >/dev/null; then
  echo "    strace absent — mesure obligatoire sur Linux, ne pas conclure"
  exit 1
fi
strace -f -e trace=execve,socket,connect -o "$WS/tr.txt" "$AEF" --workspace "$WS" --json doctor >/dev/null 2>&1 || true
execve_n=$(grep -c execve "$WS/tr.txt" || true)
socket_n=$(grep -c 'socket(' "$WS/tr.txt" || true)
connect_n=$(grep -c 'connect(' "$WS/tr.txt" || true)
echo "    execve=$execve_n socket=$socket_n connect=$connect_n"
echo "    CONTROLE POSITIF de l'instrumentation (attendu execve>=2, socket>=1) :"
strace -f -e trace=execve,socket -o "$WS/pc.txt" python3 -c "import socket,subprocess;socket.socket();subprocess.run(['/bin/true'])" 2>/dev/null || true
pc_execve=$(grep -c execve "$WS/pc.txt" || true)
pc_socket=$(grep -c 'socket(' "$WS/pc.txt" || true)
echo "    execve=$pc_execve socket=$pc_socket"
if [ "$pc_execve" -lt 2 ] || [ "$pc_socket" -lt 1 ]; then
  echo "    *** instrumentation strace ne discrimine pas ***"
  fail=1
fi
if [ "$socket_n" -ne 0 ] || [ "$connect_n" -ne 0 ]; then
  echo "    *** acces reseau observe ***"
  fail=1
fi
if [ "$execve_n" -ne 1 ]; then
  echo "    *** execve=$execve_n (attendu 1) ***"
  fail=1
fi
if [ -f "$WS/PWNED" ]; then
  echo "    temoin d'execution present ? OUI — REGRESSION"
  fail=1
else
  echo "    temoin d'execution present ? non"
fi
exit "$fail"
