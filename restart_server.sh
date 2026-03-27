#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
WAIT_SECONDS="${RESTART_WAIT_SECONDS:-15}"
TERM_WAIT_SECONDS="${RESTART_TERM_WAIT_SECONDS:-5}"
RUNTIME_DIR="${RUNTIME_DIR:-$(pwd)/runtime}"
INSTANCE_LOCK_FILE="${INSTANCE_LOCK_FILE:-${RUNTIME_DIR}/instance.lock}"

log() {
  printf '[restart_server] %s\n' "$*"
}

list_matching_pids() {
  ps -ef | awk '
    /uvicorn/ && /knowledge_miner\.main:app/ && !/awk/ {
      print $2
    }
  ' || true
}

list_port_listener_pids() {
  lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
}

list_instance_lock_holder_pids() {
  if [ -e "$INSTANCE_LOCK_FILE" ]; then
    lsof -t "$INSTANCE_LOCK_FILE" 2>/dev/null || true
  fi
}

list_target_pids() {
  {
    list_matching_pids
    list_port_listener_pids
    list_instance_lock_holder_pids
  } | awk 'NF {print $1}' | sort -u
}

join_pid_lines() {
  tr '\n' ' ' | sed 's/[[:space:]]*$//'
}

signal_target_pids() {
  local signal="$1"
  local pids="$2"
  [ -z "$pids" ] && return 0
  while read -r pid; do
    [ -z "$pid" ] && continue
    kill "$signal" "$pid" 2>/dev/null || true
  done <<< "$pids"
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
log "Runtime lock file: ${INSTANCE_LOCK_FILE}"

STALE_APP_PIDS="$(list_matching_pids)"
if [ -n "$STALE_APP_PIDS" ]; then
  log "Force-killing stale Knowledge Miner uvicorn PID(s): $(printf '%s\n' "$STALE_APP_PIDS" | join_pid_lines)"
  signal_target_pids "-KILL" "$STALE_APP_PIDS"
  sleep 1
fi

TARGET_PIDS="$(list_target_pids)"
if [ -n "$TARGET_PIDS" ]; then
  BEFORE_PIDS="$(printf '%s\n' "$TARGET_PIDS" | join_pid_lines)"
  log "Target PID(s) for shutdown: ${BEFORE_PIDS}"
  log "Sending SIGTERM to matching process, port-listener, and instance-lock holder PID(s)."
  signal_target_pids "-TERM" "$TARGET_PIDS"

  AFTER_KILL_PIDS="$(list_target_pids | join_pid_lines)"
  if [ -n "${AFTER_KILL_PIDS}" ]; then
    log "PID(s) still present immediately after SIGTERM: ${AFTER_KILL_PIDS}"
  else
    log "No matching PID remains immediately after SIGTERM."
  fi

  if ! wait_for_port_release "$TERM_WAIT_SECONDS" "SIGTERM"; then
    REMAINING_AFTER_TERM="$(list_target_pids | join_pid_lines)"
    if [ -n "${REMAINING_AFTER_TERM}" ]; then
      log "PID(s) still present after SIGTERM grace period: ${REMAINING_AFTER_TERM}"
      log "Escalating to SIGKILL."
      signal_target_pids "-KILL" "$(list_target_pids)"
      FINAL_PIDS="$(list_target_pids | join_pid_lines)"
      if [ -n "${FINAL_PIDS}" ]; then
        log "PID(s) still present immediately after SIGKILL: ${FINAL_PIDS}"
      else
        log "No matching PID remains immediately after SIGKILL."
      fi
      wait_for_port_release "$WAIT_SECONDS" "SIGKILL" || true
    fi
  fi
else
  log "No existing matching process, port listener, or instance-lock holder detected."
fi

if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  REMAINING_PIDS="$(list_target_pids | join_pid_lines)"
  log "Port ${PORT} is still busy after waiting ${WAIT_SECONDS}s."
  if [ -n "${REMAINING_PIDS}" ]; then
    log "Remaining target PID(s): ${REMAINING_PIDS}"
  fi
  lsof -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 1
fi

log "Starting run_server.sh"
"$(dirname "$0")/run_server.sh"
