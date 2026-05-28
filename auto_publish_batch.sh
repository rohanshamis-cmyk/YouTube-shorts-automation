#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: ./auto_publish_batch.sh <count> [\"topic\"] [--start-date YYYY-MM-DD] [--limit N] [--execute]" >&2
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

COUNT="$1"
shift

TOPIC=""
START_DATE=""
LIMIT=""
EXECUTE=0

if [[ $# -gt 0 && "$1" != "--start-date" && "$1" != "--limit" && "$1" != "--execute" ]]; then
  TOPIC="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-date)
      if [[ $# -lt 2 ]]; then
        usage
      fi
      START_DATE="$2"
      shift 2
      ;;
    --execute)
      EXECUTE=1
      shift
      ;;
    --limit)
      if [[ $# -lt 2 ]]; then
        usage
      fi
      LIMIT="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing project Python: $PYTHON_BIN" >&2
  exit 1
fi

TMP_LOG="$(mktemp)"
trap 'rm -f "$TMP_LOG"' EXIT

echo "=== Stage 1/4: generating batch ==="
GEN_CMD=("$SCRIPT_DIR/run_batch.sh" "$COUNT")
if [[ -n "$TOPIC" ]]; then
  GEN_CMD+=("$TOPIC")
fi
if [[ -n "$START_DATE" ]]; then
  GEN_CMD+=("--start-date" "$START_DATE")
fi

if ! "${GEN_CMD[@]}" 2>&1 | tee "$TMP_LOG"; then
  echo "Generation stage failed." >&2
  exit 1
fi

BATCH_DIR="$($PYTHON_BIN - <<'PY'
from pathlib import Path
batches = [p for p in Path('drafts').glob('batch_*') if p.is_dir()]
print(max(batches, key=lambda p: p.stat().st_mtime) if batches else "")
PY
)"

if [[ -z "$BATCH_DIR" || ! -d "$BATCH_DIR" ]]; then
  echo "Could not determine newest batch folder after generation." >&2
  exit 1
fi

LOG_FILE="$BATCH_DIR/automation_log_$(date +%Y%m%d_%H%M%S).log"
cat "$TMP_LOG" > "$LOG_FILE"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

log "=== Stage 2/4: rendering batch ==="
if ! "$SCRIPT_DIR/render_latest_batch.sh" "$BATCH_DIR" 2>&1 | tee -a "$LOG_FILE"; then
  log "Rendering stage failed."
  exit 1
fi

log "=== Stage 3/4: uploading videos ==="
log "=== Stage 4/4: scheduling by posting order ==="
UPLOAD_CMD=("$SCRIPT_DIR/upload_latest_batch.sh" "--batch-dir" "$BATCH_DIR" "--privacy" "private" "--schedule")
if [[ -n "$START_DATE" ]]; then
  UPLOAD_CMD+=("--start-date" "$START_DATE")
fi
if [[ -n "$LIMIT" ]]; then
  UPLOAD_CMD+=("--limit" "$LIMIT")
fi
if [[ "$EXECUTE" -eq 1 ]]; then
  UPLOAD_CMD+=("--execute")
fi

if ! "${UPLOAD_CMD[@]}" 2>&1 | tee -a "$LOG_FILE"; then
  log "Upload/scheduling stage failed."
  exit 1
fi

log "Automation complete."
log "Batch folder: $BATCH_DIR"
log "Automation log: $LOG_FILE"
