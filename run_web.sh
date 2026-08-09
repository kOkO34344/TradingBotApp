#!/bin/bash
# run_web.sh — start the local web UI (FastAPI backend + Next.js frontend).
#
# Both processes bind to 127.0.0.1 only. This is deliberate and must stay
# that way: the backend holds a live IB Gateway connection on its own
# clientId and can place orders, so binding it to 0.0.0.0 would put an
# unauthenticated order path on whatever network this laptop is joined to.
#
#   ./run_web.sh          start both, stream logs
#   ./run_web.sh api      backend only
#   ./run_web.sh web      frontend only
#
# Ctrl-C stops both.

set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"
API_PORT=8000
WEB_PORT=3000

# Same reason trader_app.sh pins the venv: `python3` on this machine resolves
# to conda base, which has pandas and yfinance but not the rest, so the app
# starts and then fails somewhere confusing.
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: $VENV_PY not found. Create the venv first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if ! "$VENV_PY" -c "import fastapi" 2>/dev/null; then
  echo "ERROR: fastapi is not installed in .venv. Run:"
  echo "  .venv/bin/pip install fastapi uvicorn"
  exit 1
fi

if [ ! -d "web/node_modules" ]; then
  echo "ERROR: web/node_modules missing. Run:  cd web && npm install"
  exit 1
fi

MODE="${1:-both}"
PIDS=()

cleanup() {
  echo ""
  echo "Stopping…"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

start_api() {
  echo "API      http://127.0.0.1:$API_PORT   (IB Gateway clientId 15)"
  "$VENV_PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$API_PORT" &
  PIDS+=($!)
}

start_web() {
  echo "UI       http://localhost:$WEB_PORT"
  (cd web && npm run dev) &
  PIDS+=($!)
}

case "$MODE" in
  api) start_api ;;
  web) start_web ;;
  both)
    start_api
    sleep 2
    start_web
    ;;
  *)
    echo "Usage: ./run_web.sh [api|web|both]"
    exit 1
    ;;
esac

echo ""
echo "The UI refuses to render trading controls unless the backend has"
echo "verified a PAPER account id. Gateway down? Screens still load and say so."
echo "Ctrl-C to stop."
wait
