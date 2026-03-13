#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_PATTERN="[u]vicorn knowledge_miner.main:app"
PORT="${PORT:-8000}"
WAIT_SECONDS="${RESTART_WAIT_SECONDS:-15}"
TERM_WAIT_SECONDS="${RESTART_TERM_WAIT_SECONDS:-5}"

log() {
  printf '[restart_server] %s\n' "$*"
}

list_matching_pids() {
  pgrep -f "$APP_PATTERN" || true
}

wait_for_port_release() {
  local wait_seconds="$1"
  local phase="$2"
  for ((i=0; i<wait_seconds; i++)); do
    if ! lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      log "Port ${PORT} released during ${phase} after $((i + 1))s."
      return 0
    fi
    log "Waiting for port ${PORT} to be released during ${phase}... (${i}/${wait_seconds})"
    sleep 1
  done
  return 1
}

log "Starting restart flow in $(pwd)"
log "Target port: ${PORT}"

if pgrep -f "$APP_PATTERN" >/dev/null 2>&1; then
  BEFORE_PIDS="$(list_matching_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  log "Existing uvicorn PID(s): ${BEFORE_PIDS}"
  log "Existing uvicorn process detected. Sending SIGTERM."
  pkill -f "$APP_PATTERN"

  AFTER_KILL_PIDS="$(list_matching_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [ -n "${AFTER_KILL_PIDS}" ]; then
    log "PID(s) still present immediately after SIGTERM: ${AFTER_KILL_PIDS}"
  else
    log "No matching PID remains immediately after SIGTERM."
  fi

  if ! wait_for_port_release "$TERM_WAIT_SECONDS" "SIGTERM"; then
    REMAINING_AFTER_TERM="$(list_matching_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    if [ -n "${REMAINING_AFTER_TERM}" ]; then
      log "PID(s) still present after SIGTERM grace period: ${REMAINING_AFTER_TERM}"
      log "Escalating to SIGKILL."
      pkill -9 -f "$APP_PATTERN"
      FINAL_PIDS="$(list_matching_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
      if [ -n "${FINAL_PIDS}" ]; then
        log "PID(s) still present immediately after SIGKILL: ${FINAL_PIDS}"
      else
        log "No matching PID remains immediately after SIGKILL."
      fi
      wait_for_port_release "$WAIT_SECONDS" "SIGKILL" || true
    fi
  fi
else
  log "No existing uvicorn process detected."
fi

if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  REMAINING_PIDS="$(list_matching_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  log "Port ${PORT} is still busy after waiting ${WAIT_SECONDS}s."
  if [ -n "${REMAINING_PIDS}" ]; then
    log "Matching uvicorn PID(s) still present: ${REMAINING_PIDS}"
  fi
  lsof -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 1
fi

log "Starting run_server.sh"
"$(dirname "$0")/run_server.sh"
