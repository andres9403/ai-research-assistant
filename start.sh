#!/usr/bin/env bash
# Set up and start the whole app at http://localhost:8000 from one terminal.
# The first run creates backend/.venv, installs the dependencies and builds the
# frontend; later runs reuse them and reinstall only what changed.
# Port 8000 is used if it is free, otherwise the next free one up to 8020; the
# script prints the link to open.
# Usage: ./start.sh            (PORT=8001 ./start.sh to use that port only)
set -euo pipefail

cd "$(dirname "$0")"

fail() {
  echo "start.sh: $*" >&2
  exit 1
}

# Python 3.11+ that can create a venv. macOS's default python3 can be 3.9, so try
# newer names first. Skip Pythons bundled with other tools (e.g. Gurobi) or
# Debian's without python3-venv, which lack the venv or ensurepip module.
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
[ -n "$PYTHON" ] || fail "Python 3.11 or newer with the venv module is required. Install it from https://www.python.org/downloads/ (on Debian/Ubuntu also: sudo apt install python3-venv) and run this again."

command -v node >/dev/null 2>&1 || fail "Node.js 18 or newer is required. Install it from https://nodejs.org/ and run this again."
node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 18 ? 1 : 0)' ||
  fail "Node.js 18 or newer is required; found $(node --version)."

VENV_PY=backend/.venv/bin/python
if [ -x "$VENV_PY" ] && ! "$VENV_PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  fail "backend/.venv was made with an older Python. Delete backend/.venv and run this again."
fi
if [ ! -x "$VENV_PY" ]; then
  echo "Creating backend/.venv with $("$PYTHON" --version)…"
  "$PYTHON" -m venv backend/.venv
fi

# Reinstall only when requirements.txt changed since the last successful install.
STAMP=backend/.venv/requirements.installed
if ! cmp -s backend/requirements.txt "$STAMP"; then
  echo "Installing backend dependencies…"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r backend/requirements.txt
  cp backend/requirements.txt "$STAMP"
fi

if [ ! -d frontend/node_modules ] || [ frontend/package-lock.json -nt frontend/node_modules ]; then
  echo "Installing frontend dependencies…"
  (cd frontend && npm ci --no-audit --no-fund)
  touch frontend/node_modules
fi
echo "Building the frontend…"
(cd frontend && npm run build --silent)

# Print the candidate ports that nothing listens on here, in order. SO_REUSEADDR
# matches uvicorn, so a port left in TIME_WAIT by the last run still counts as free.
free_here() {
  "$PYTHON" - "$@" <<'EOF'
import socket, sys
for port in sys.argv[1:]:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", int(port)))
        except OSError:
            continue
    print(port)
EOF
}

# Under WSL the browser runs on Windows, which can hold or reserve a port that is
# free inside WSL (port 8000 is sometimes reserved for HTTP.sys). Keep only the
# ports Windows can bind too; if Windows can't be asked, keep them all.
free_on_windows() {
  if grep -qi microsoft /proc/version 2>/dev/null && command -v powershell.exe >/dev/null 2>&1; then
    local ports
    ports=$(echo "$@" | tr ' ' ',')
    timeout 20 powershell.exe -NoProfile -NonInteractive -Command "foreach (\$p in $ports) { try { \$l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, \$p); \$l.Start(); \$l.Stop(); \$p } catch {} }; exit 0" 2>/dev/null | tr -d '\r' && return
  fi
  printf '%s\n' "$@"
}

if [ -n "${PORT:-}" ]; then
  free=$(free_here "$PORT")
  [ -n "$free" ] && free=$(free_on_windows "$PORT")
  [ -n "$free" ] || fail "Port $PORT is in use. Run ./start.sh without PORT to pick a free port, or choose another one."
else
  free=$(free_here $(seq 8000 8020))
  [ -n "$free" ] && free=$(free_on_windows $free)
  PORT=$(echo "$free" | head -n 1)
  [ -n "$PORT" ] || fail "Ports 8000 to 8020 are all in use. Run PORT=<number> ./start.sh with a free port."
  [ "$PORT" = 8000 ] || echo "Port 8000 is in use, so the app will use port $PORT."
fi

echo
echo "Starting the app. Open http://localhost:$PORT when you see 'Application startup complete'."
echo "Press Ctrl+C to stop it."
echo
cd backend
exec .venv/bin/python -m uvicorn app.main:app --port "$PORT"
