#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_TOKEN="${API_TOKEN:-dev-token}"
RUN_ID="${1:-}"
SOURCE_ID="${2:-}"
ITERATIONS="${ITERATIONS:-30}"
SLEEP_SECONDS="${SLEEP_SECONDS:-2}"
OUT_DIR="${OUT_DIR:-./runtime/diagnostics}"

if [[ -z "$RUN_ID" || -z "$SOURCE_ID" ]]; then
  echo "Usage: $0 <run_id> <source_id>"
  echo "Env: BASE_URL API_TOKEN ITERATIONS SLEEP_SECONDS OUT_DIR"
  exit 1
fi

mkdir -p "$OUT_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$OUT_DIR/db_flap_trace_${TS}.log"

echo "# DB flap diagnostics" | tee -a "$OUT_FILE"
echo "base_url=$BASE_URL run_id=$RUN_ID source_id=$SOURCE_ID iterations=$ITERATIONS sleep=${SLEEP_SECONDS}s" | tee -a "$OUT_FILE"

auth=(-H "Authorization: Bearer ${API_TOKEN}")

for ((i=1; i<=ITERATIONS; i++)); do
  now="$(date --iso-8601=seconds)"
  echo "\n## sample=$i ts=$now" | tee -a "$OUT_FILE"

  status_code=$(curl -sS -o /tmp/km_run_resp.json -w "%{http_code}" "${auth[@]}" "$BASE_URL/v1/discovery/runs/$RUN_ID" || true)
  echo "run_status_code=$status_code" | tee -a "$OUT_FILE"
  sed -n '1,1p' /tmp/km_run_resp.json | tr -d '\n' | sed 's/^/run_body=/' | tee -a "$OUT_FILE"
  echo | tee -a "$OUT_FILE"

  review_code=$(curl -sS -o /tmp/km_review_resp.json -w "%{http_code}" "${auth[@]}" -H "Content-Type: application/json" -X POST "$BASE_URL/v1/sources/$SOURCE_ID/review" -d '{"decision":"accept"}' || true)
  echo "review_status_code=$review_code" | tee -a "$OUT_FILE"
  sed -n '1,1p' /tmp/km_review_resp.json | tr -d '\n' | sed 's/^/review_body=/' | tee -a "$OUT_FILE"
  echo | tee -a "$OUT_FILE"

  sys_code=$(curl -sS -o /tmp/km_sys_resp.json -w "%{http_code}" "${auth[@]}" "$BASE_URL/v1/system/status" || true)
  echo "system_status_code=$sys_code" | tee -a "$OUT_FILE"
  sed -n '1,1p' /tmp/km_sys_resp.json | tr -d '\n' | sed 's/^/system_body=/' | tee -a "$OUT_FILE"
  echo | tee -a "$OUT_FILE"

  dbg_code=$(curl -sS -o /tmp/km_dbg_resp.json -w "%{http_code}" "${auth[@]}" "$BASE_URL/v1/debug/db-context?run_id=$RUN_ID&source_id=$SOURCE_ID" || true)
  echo "debug_status_code=$dbg_code" | tee -a "$OUT_FILE"
  sed -n '1,1p' /tmp/km_dbg_resp.json | tr -d '\n' | sed 's/^/debug_body=/' | tee -a "$OUT_FILE"
  echo | tee -a "$OUT_FILE"

  sleep "$SLEEP_SECONDS"
done

echo "Saved: $OUT_FILE"
