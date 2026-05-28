#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing project Python: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/upload_batch.py" "$@"
