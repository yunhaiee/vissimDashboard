#!/usr/bin/env bash
#
# One command to put Prism in front of someone outside the team.
#
#   ./scripts/demo.sh
#
# Does the whole setup and then opens the password-gated tunnel: creates the
# venv if it is missing, installs backend and frontend dependencies, seeds
# synthetic demo data if there is none, and hands over to
# scripts/share_demo.py. Everything here is idempotent -- a second run skips
# straight to the tunnel in a couple of seconds.
#
# Written 2026-09-08 because the documented three-step version was three steps
# too many on a laptop that turned out to have no venv at all (the cause: an
# unmarked pywin32 in backend/requirements.txt made `pip install` fail
# outright on macOS -- see that file).
#
# Arguments are passed straight through to share_demo.py, so
# `./scripts/demo.sh --user prof --password hunter2` works.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv"
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mx %s\033[0m\n' "$1" >&2; exit 1; }

# --- 1. an interpreter ------------------------------------------------------
# macOS ships python3 and NOT python; that missing alias is the first thing
# that bit on 2026-09-08.
PYTHON_BOOTSTRAP="$(command -v python3 || true)"
[ -n "$PYTHON_BOOTSTRAP" ] || fail "python3 not found. Install it: brew install python"

# --- 2. the venv ------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  step "Creating the virtualenv (.venv) -- first run only"
  "$PYTHON_BOOTSTRAP" -m venv "$VENV"
fi
PY="$VENV/bin/python"

# --- 3. backend dependencies ------------------------------------------------
# fastapi is the cheap proxy for "are the requirements installed": it is the
# first line of the file and nothing else pulls it in.
if ! "$PY" -c "import fastapi" >/dev/null 2>&1; then
  step "Installing backend dependencies -- first run only, a few minutes"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r backend/requirements.txt
fi

# --- 4. frontend dependencies ----------------------------------------------
command -v npm >/dev/null 2>&1 || fail "npm not found. Install Node: brew install node"
if [ ! -d "$REPO_ROOT/frontend/node_modules" ]; then
  step "Installing frontend dependencies -- first run only"
  (cd frontend && npm install --no-audit --no-fund)
fi

# --- 5. demo data -----------------------------------------------------------
# Only when there is none. A default --out run rmtree's data/Scenarios, so
# re-seeding an existing install is never done behind the operator's back.
if [ ! -d "$REPO_ROOT/data/Scenarios" ]; then
  step "Seeding synthetic demo data"
  # -m from backend/, not a path: a path run puts backend/tools on sys.path
  # instead of backend/, and the script's `from services.workspaces import ...`
  # dies with ModuleNotFoundError.
  (cd backend && "$PY" -m tools.make_demo_data)
else
  echo "Demo data already present in data/ -- leaving it alone."
fi

# --- 6. the tunnel binary ---------------------------------------------------
if ! command -v cloudflared >/dev/null 2>&1; then
  fail "cloudflared not found. Install it, then re-run this script:

    brew install cloudflared

  No Cloudflare account or domain is needed."
fi

# --- 7. hand over -----------------------------------------------------------
step "Starting Prism and opening the tunnel"
exec "$PY" scripts/share_demo.py "$@"
