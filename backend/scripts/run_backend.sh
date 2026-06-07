#!/usr/bin/env bash
# Start FastAPI + Nautilus (logs → backend/logs/uvicorn.log, Nautilus WARN by default)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

# shellcheck source=_log_helpers.sh
source "$(dirname "$0")/_log_helpers.sh"

LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/uvicorn.log"
mkdir -p "$LOG_DIR"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env"
  set +a
fi

VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing $ROOT/.venv — run: python -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi

export PYTHONPATH="$ROOT"
export NAUTILUS_LOG_LEVEL="${NAUTILUS_LOG_LEVEL:-WARNING}"
PORT="${PORT:-8000}"
# RELOAD=1 restarts uvicorn on .py changes and kills the Nautilus node mid-flush.
RELOAD="${RELOAD:-0}"

_prune_junk_logs "$LOG_DIR"
_maybe_rotate_log "$LOG_FILE"

echo "Backend log: $LOG_FILE (max ${LOG_MAX_MB}MB, Nautilus level=$NAUTILUS_LOG_LEVEL)"
echo "Follow: tail -f $LOG_FILE"
echo "Stop: Ctrl+C"
echo "---"

ARGS=(-m uvicorn main:app --host 127.0.0.1 --port "$PORT" "${UVICORN_LOG_ARGS[@]}")
if [[ "$RELOAD" == "1" ]]; then
  ARGS+=(--reload)
fi

"$VENV_PY" "${ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
