#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
HEALTH_URL="http://${HOST}:${PORT}/healthz"

if curl -fsS "$HEALTH_URL"; then
  echo
  echo "Healthy server detected at ${HEALTH_URL}"
  exit 0
fi

if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${PORT} is busy, but the expected health endpoint is not responding."
  lsof -iTCP:"$PORT" -sTCP:LISTEN
  exit 1
fi

echo "Nothing is listening on ${HOST}:${PORT}"
