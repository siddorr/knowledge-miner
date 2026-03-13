#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
HEALTHCHECK_HOST="${HEALTHCHECK_HOST:-127.0.0.1}"
HEALTH_URL="http://${HEALTHCHECK_HOST}:${PORT}/healthz"
APP_MODULE="knowledge_miner.main:app"
LOG_FILE="knowledge-miner-uvicorn.log"
CURL_MAX_TIME="${CURL_MAX_TIME:-5}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-20}"

log() {
  printf '[run_server] %s\n' "$*"
}

healthcheck() {
  curl --fail --silent --show-error --max-time "$CURL_MAX_TIME" "$HEALTH_URL"
}

log "Starting server flow in $(pwd)"
log "Bind address: ${HOST}:${PORT}"
log "Healthcheck URL: ${HEALTH_URL}"
log "Log file: ${LOG_FILE}"

# If the expected app is already healthy, do nothing.
if healthcheck >/dev/null 2>&1; then
  log "Server already running and healthy at ${HEALTH_URL}"
  exit 0
fi

# If something else is listening on the port, refuse to kill it blindly.
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  log "Port ${PORT} is already in use by another process. Refusing to start."
  lsof -iTCP:"$PORT" -sTCP:LISTEN
  exit 1
fi

if [ ! -d .venv ]; then
  log "Missing .venv in project root."
  exit 1
fi

log "Activating virtual environment and loading .env"
source .venv/bin/activate
set -a
[ -f .env ] && source .env || true
set +a

log "Launching uvicorn"
nohup python -m uvicorn "$APP_MODULE" \
  --host "$HOST" \
  --port "$PORT" \
  > "$LOG_FILE" 2>&1 &

UVICORN_PID=$!
log "uvicorn started with PID ${UVICORN_PID}"

for ((i=0; i<STARTUP_WAIT_SECONDS; i++)); do
  if healthcheck >/dev/null 2>&1; then
    log "Healthcheck passed after $((i + 1))s."
    healthcheck
    echo
    log "Server started successfully."
    exit 0
  fi
  log "Waiting for healthcheck... (${i}/${STARTUP_WAIT_SECONDS})"
  sleep 1
done

log "Server did not become healthy. Last log lines:"
tail -n 30 "$LOG_FILE" || true
exit 1
