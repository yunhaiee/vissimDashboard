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

# --- 1. an interpreter NEW ENOUGH FOR THE PINS ------------------------------
# macOS ships python3 and NOT python, and the python3 it ships can be 3.9 --
# both bit on 2026-09-08. 3.10 is the real floor: matplotlib==3.10.6 and
# scikit-learn==1.7.2 both declare Requires-Python >=3.10, and on 3.9 pip does
# not say "too old", it silently hunts for an ancient matplotlib and dies with
# "No matching distribution found", which reads like a broken pin.
# Newest first, so a box with several installed uses the best one.
python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

PYTHON_BOOTSTRAP=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  candidate_path="$(command -v "$candidate" 2>/dev/null || true)"
  [ -n "$candidate_path" ] || continue
  if python_ok "$candidate_path"; then
    PYTHON_BOOTSTRAP="$candidate_path"
    break
  fi
done

if [ -z "$PYTHON_BOOTSTRAP" ]; then
  found="$(command -v python3 >/dev/null 2>&1 && python3 --version 2>&1 || echo 'none found')"
  fail "Python 3.10 or newer is required; this machine has: $found

    brew install python@3.12

  Then re-run this script -- it picks the newest python3.N on PATH."
fi
echo "Using $("$PYTHON_BOOTSTRAP" --version 2>&1) at $PYTHON_BOOTSTRAP"

# --- 2. the venv ------------------------------------------------------------
# A venv built by a too-old interpreter is worse than none: it exists, so the
# check below would skip creation, and every install into it fails the same way
# forever. Rebuild it instead of leaving the operator to guess.
if [ -x "$VENV/bin/python" ] && ! python_ok "$VENV/bin/python"; then
  step "Rebuilding .venv -- it was built by $("$VENV/bin/python" --version 2>&1), which is too old"
  case "$VENV" in
    */.venv) rm -rf "$VENV" ;;
    *) fail "Refusing to remove an unexpected venv path: $VENV" ;;
  esac
fi

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
