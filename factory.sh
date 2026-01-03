#!/bin/zsh
set -euo pipefail

# Mend Video Factory launcher
# Usage:
#   ./factory.sh                 # start Jobs UI (web.app) on http://127.0.0.1:8000
#   ./factory.sh web             # same as default
#   ./factory.sh simple          # start simple UI (app.main)
#   ./factory.sh chapter 01      # build chapter 01 via CLI (no web)
#   ./factory.sh storyboard 01   # generate out/<CH>/storyboard.json from script.txt (no AI)
#   ./factory.sh batch 01 02 03  # build multiple chapters via CLI (no web)

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# Load local env vars (recommended for secrets).
# Create `.env.local` in repo root:
#   WEB_USER="..."
#   WEB_PASS="..."
#
# This avoids needing to export vars every time and ensures the web server sees them.
if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  source "$ROOT_DIR/.env.local"
  set +a
elif [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

MODE="${1:-web}"
shift || true

# Optional: don't auto-open browser (useful for auto-start / LaunchAgent)
NO_BROWSER=0
if [[ "${1:-}" == "--no-browser" ]]; then
  NO_BROWSER=1
  shift || true
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT_DIR/.venv"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo ">> Creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

PY="$VENV_DIR/bin/python"

echo ">> Installing/updating requirements..."
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements.txt

# Optional: install heavy TTS deps only when enabled.
# (Coqui XTTS v2 uses torch and is intentionally not part of default requirements.)
if [[ "${TTS_ENGINE:-}" == "coqui_xtts_v2" || "${TTS_ENGINE:-}" == "xtts_v2" || "${TTS_ENGINE:-}" == "xtts" || "${TTS_ENGINE:-}" == "" ]]; then
  # We only check for Python 3.11 if we are explicitly using XTTS or default engine.
  PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "$PYVER" == "3.12" || "$PYVER" == "3.13" ]]; then
    echo "ERROR: Coqui XTTS v2 (pip package 'TTS') requires Python 3.11 (Python < 3.12)." >&2
    echo "Your venv is Python $PYVER." >&2
    echo "" >&2
    echo "Fix (recommended):" >&2
    echo "  brew install python@3.11" >&2
    echo "  rm -rf .venv" >&2
    echo "  PYTHON_BIN=python3.11 ./factory.sh web" >&2
    exit 2
  fi
  if [[ -f "$ROOT_DIR/requirements_tts_coqui.txt" ]]; then
    echo ">> TTS_ENGINE=${TTS_ENGINE:-coqui_xtts_v2} → Installing optional Coqui XTTS v2 deps..."
    "$PY" -m pip install -r requirements_tts_coqui.txt
  fi
fi

case "$MODE" in
  web)
    # Choose a port. Default 8000, but auto-fallback if busy.
    PORT="${MEND_PORT:-8000}"
    BASE_HOST="127.0.0.1"
    # If port is busy, try next ports.
    if command -v lsof >/dev/null 2>&1; then
      for i in {0..20}; do
        TRY_PORT=$((PORT + i))
        if ! lsof -nP -iTCP:"$TRY_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
          PORT="$TRY_PORT"
          break
        fi
      done
    fi
    echo ">> Starting Jobs UI: http://$BASE_HOST:$PORT"
    if [[ "$NO_BROWSER" -eq 0 ]]; then
      open "http://$BASE_HOST:$PORT" >/dev/null 2>&1 || true
    fi
    exec "$PY" -m uvicorn web.app:app --host "$BASE_HOST" --port "$PORT" --reload
    ;;
  simple)
    PORT="${MEND_PORT:-8000}"
    BASE_HOST="127.0.0.1"
    if command -v lsof >/dev/null 2>&1; then
      for i in {0..20}; do
        TRY_PORT=$((PORT + i))
        if ! lsof -nP -iTCP:"$TRY_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
          PORT="$TRY_PORT"
          break
        fi
      done
    fi
    echo ">> Starting Simple UI: http://$BASE_HOST:$PORT"
    if [[ "$NO_BROWSER" -eq 0 ]]; then
      open "http://$BASE_HOST:$PORT" >/dev/null 2>&1 || true
    fi
    exec "$PY" -m uvicorn app.main:app --host "$BASE_HOST" --port "$PORT" --reload
    ;;
  chapter)
    CH="${1:-}"
    if [[ -z "$CH" ]]; then
      echo "ERROR: missing chapter id. Example: ./factory.sh chapter 01" >&2
      exit 2
    fi
    shift || true
    exec "$PY" scripts/build_chapter.py --project "$ROOT_DIR" --chapter "$CH" --burn_subs "$@"
    ;;
  storyboard)
    CH="${1:-}"
    if [[ -z "$CH" ]]; then
      echo "ERROR: missing chapter id. Example: ./factory.sh storyboard 01" >&2
      exit 2
    fi
    shift || true
    exec "$PY" scripts/storyboard_rules.py --project "$ROOT_DIR" --chapter "$CH" "$@"
    ;;
  batch)
    # Optional: pass chapter list. If none provided, batch script auto-detects.
    if [[ $# -gt 0 ]]; then
      exec "$PY" scripts/scripts/batch_generate.py --project "$ROOT_DIR" --chapters "$@" --burn_subs
    else
      exec "$PY" scripts/scripts/batch_generate.py --project "$ROOT_DIR" --burn_subs
    fi
    ;;
  *)
    echo "ERROR: unknown mode: $MODE" >&2
    echo "Valid: web | simple | chapter | batch" >&2
    exit 2
    ;;
esac


