#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Missing .venv in project root."
  exit 1
fi

source .venv/bin/activate
set -a
[ -f .env ] && source .env || true
set +a

python scripts/manual_live_logic_test.py "$@"
