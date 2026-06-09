#!/usr/bin/env bash
# Sürekli katalog kaydı: backend + Nautilus node arka planda, çökünce yeniden başlar.
#
#   ./scripts/run_catalog_recorder_daemon.sh start
#   ./scripts/run_catalog_recorder_daemon.sh stop
#   ./scripts/run_catalog_recorder_daemon.sh status
#   ./scripts/run_catalog_recorder_daemon.sh rotate-logs   # truncate uvicorn.log
#   ./scripts/run_catalog_recorder_daemon.sh cleanup-logs  # remove junk + rotate if huge
#
# Logs (see backend/logs/README.md):
#   uvicorn.log         — app + Nautilus (NAUTILUS_LOG_LEVEL=ERROR by default)
#   recorder-daemon.log — supervisor start/stop/restart only
#
# Önemli: RELOAD=0 (varsayılan). RELOAD=1 parquet flush'ı keser.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

# shellcheck source=_log_helpers.sh
source "$(dirname "$0")/_log_helpers.sh"

LOG_DIR="$ROOT/logs"
RUN_DIR="$LOG_DIR/run"
DAEMON_LOG="$LOG_DIR/recorder-daemon.log"
PID_FILE="$RUN_DIR/catalog-recorder.pid"
SUPERVISOR_PID="$RUN_DIR/catalog-recorder-supervisor.pid"
UVICORN_LOG="$LOG_DIR/uvicorn.log"

mkdir -p "$LOG_DIR" "$RUN_DIR"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env"
  set +a
fi

export CATALOG_STREAMING_ENABLED="${CATALOG_STREAMING_ENABLED:-true}"
export RELOAD=0
export POLYMARKET_DATA_ENABLED="${POLYMARKET_DATA_ENABLED:-true}"
export NAUTILUS_LOG_LEVEL="${NAUTILUS_LOG_LEVEL:-ERROR}"

VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing $ROOT/.venv" >&2
  exit 1
fi

_stop_supervisor() {
  if [[ -f "$SUPERVISOR_PID" ]]; then
    local pid
    pid="$(cat "$SUPERVISOR_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$SUPERVISOR_PID"
  fi
  # Orphan supervisors from older starts (before singleton guard).
  local pid
  while read -r pid; do
    [[ -z "$pid" || "$pid" == "$$" ]] && continue
    kill "$pid" 2>/dev/null || true
  done < <(pgrep -f "run_catalog_recorder_daemon.sh start" 2>/dev/null || true)
}

_stop_uvicorn() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 2
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  pkill -f "uvicorn main:app --host 127.0.0.1" 2>/dev/null || true
}

_supervisor_running() {
  [[ -f "$SUPERVISOR_PID" ]] && kill -0 "$(cat "$SUPERVISOR_PID")" 2>/dev/null
}

_start_once() {
  _maybe_rotate_log "$UVICORN_LOG" "$DAEMON_LOG"
  export PYTHONPATH="$ROOT"
  PORT="${PORT:-8000}"
  nohup "$VENV_PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
    "${UVICORN_LOG_ARGS[@]}" \
    >>"$UVICORN_LOG" 2>&1 &
  echo $! >"$PID_FILE"
}

_ensure_instruments() {
  if [[ "${SKIP_INSTRUMENT_BOOTSTRAP:-0}" == "1" ]]; then
    return
  fi
  "$VENV_PY" "$ROOT/scripts/write_instruments_to_catalog.py" >>"$DAEMON_LOG" 2>&1 || true
}

case "${1:-start}" in
  start)
    if _supervisor_running; then
      echo "Already running (supervisor pid=$(cat "$SUPERVISOR_PID")). Use: $0 stop"
      exit 0
    fi
    _prune_junk_logs "$LOG_DIR"
    _stop_supervisor
    _stop_uvicorn
    _ensure_instruments
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] catalog recorder daemon starting" >>"$DAEMON_LOG"
    (
      while true; do
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backend run loop" >>"$DAEMON_LOG"
        _start_once
        pid="$(cat "$PID_FILE")"
        echo "Started uvicorn pid=$pid (CATALOG_STREAMING_ENABLED=$CATALOG_STREAMING_ENABLED)"
        echo "Logs: tail -f $UVICORN_LOG  (Nautilus level=$NAUTILUS_LOG_LEVEL, max ${LOG_MAX_MB}MB, rotate every ${LOG_ROTATE_INTERVAL_SEC}s)"
        while kill -0 "$pid" 2>/dev/null; do
          _maybe_rotate_log "$UVICORN_LOG" "$DAEMON_LOG"
          sleep "${LOG_ROTATE_INTERVAL_SEC}"
        done
        wait "$pid" 2>/dev/null || true
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backend exited, restart in 5s" >>"$DAEMON_LOG"
        sleep 5
      done
    ) >>"$DAEMON_LOG" 2>&1 &
    echo $! >"$SUPERVISOR_PID"
    sleep 2
    if grep -q "Catalog streaming" "$UVICORN_LOG" 2>/dev/null; then
      echo "OK: Catalog streaming enabled"
    elif grep -q "Catalog streaming" "$UVICORN_LOG" 2>/dev/null; then
      echo "OK: catalog streaming active"
    else
      echo "WARN: recorder not confirmed yet — tail -f $UVICORN_LOG"
    fi
    ;;
  stop)
    _stop_supervisor
    _stop_uvicorn
    echo "Stopped catalog recorder daemon"
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "uvicorn running pid=$(cat "$PID_FILE") (log $(_log_mb "$UVICORN_LOG")MB)"
    else
      echo "uvicorn not running"
    fi
    if [[ -f "$SUPERVISOR_PID" ]] && kill -0 "$(cat "$SUPERVISOR_PID")" 2>/dev/null; then
      echo "supervisor running pid=$(cat "$SUPERVISOR_PID")"
    fi
    ls -lh "$UVICORN_LOG" "$DAEMON_LOG" 2>/dev/null || true
    "$VENV_PY" "$ROOT/scripts/catalog_stats.py" 2>/dev/null || true
    ;;
  rotate-logs)
    _stop_supervisor
    _stop_uvicorn
    : >"$UVICORN_LOG"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] uvicorn.log truncated" >>"$DAEMON_LOG"
    _prune_junk_logs "$LOG_DIR"
    echo "Truncated $UVICORN_LOG and removed junk logs"
    ;;
  cleanup-logs)
    _stop_supervisor
    _stop_uvicorn
    _prune_junk_logs "$LOG_DIR"
    _maybe_rotate_log "$UVICORN_LOG" "$DAEMON_LOG"
    if [[ -f "$DAEMON_LOG" ]]; then
      tail -2000 "$DAEMON_LOG" >"$DAEMON_LOG.tmp" && mv "$DAEMON_LOG.tmp" "$DAEMON_LOG"
    fi
    echo "Logs cleaned. Run: $0 start"
    ls -lah "$LOG_DIR"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|rotate-logs|cleanup-logs}" >&2
    exit 1
    ;;
esac
