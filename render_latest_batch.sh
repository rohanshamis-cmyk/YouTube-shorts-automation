#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
  echo "Usage: ./render_latest_batch.sh [batch_folder]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing project Python: $PYTHON_BIN" >&2
  exit 1
fi

CMD=("$PYTHON_BIN" "$SCRIPT_DIR/render_batch.py")
if [[ $# -eq 1 ]]; then
  CMD+=("$1")
fi

exec "${CMD[@]}"
