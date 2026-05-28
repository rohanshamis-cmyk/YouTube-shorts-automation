#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"

if [[ $# -gt 1 ]]; then
  echo "Usage: ./run_dry.sh [\"topic\"]" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing project Python: $PYTHON_BIN" >&2
  exit 1
fi

CMD=("$PYTHON_BIN" "$SCRIPT_DIR/main.py" --dry-run)
if [[ $# -eq 1 ]]; then
  CMD+=(--topic "$1")
fi

exec "${CMD[@]}"
