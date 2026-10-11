#!/usr/bin/env bash
# Set up and start the whole app at http://localhost:8000 from one terminal.
# The first run creates backend/.venv, installs the dependencies and builds the
# frontend; later runs reuse them and reinstall only what changed.
# Usage: ./start.sh            (PORT=8001 ./start.sh to use another port)
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8000}"

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

echo
echo "Starting the app. Open http://localhost:$PORT when you see 'Application startup complete'."
echo "Press Ctrl+C to stop it."
echo
cd backend
exec .venv/bin/python -m uvicorn app.main:app --port "$PORT"
