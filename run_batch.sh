#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: ./run_batch.sh <count> [\"topic\"] [--start-date YYYY-MM-DD]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing project Python: $PYTHON_BIN" >&2
  exit 1
fi

COUNT="$1"
shift

TOPIC=""
START_DATE=""

if [[ $# -gt 0 && "$1" != "--start-date" ]]; then
  TOPIC="$1"
  shift
fi

if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--start-date" ]]; then
    echo "Usage: ./run_batch.sh <count> [\"topic\"] [--start-date YYYY-MM-DD]" >&2
    exit 1
  fi
  START_DATE="$2"
fi

CMD=("$PYTHON_BIN" "$SCRIPT_DIR/batch_generate.py" "$COUNT")
if [[ -n "$TOPIC" ]]; then
  CMD+=(--topic "$TOPIC")
fi
if [[ -n "$START_DATE" ]]; then
  CMD+=(--start-date "$START_DATE")
fi

exec "${CMD[@]}"
