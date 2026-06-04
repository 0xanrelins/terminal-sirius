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
#   uvicorn.log         — app + Nautilus (NAUTILUS_LOG_LEVEL=WARN by default)
#   recorder-daemon.log — supervisor start/stop/restart only
#
# Önemli: RELOAD=0 (varsayılan). RELOAD=1 parquet flush'ı keser.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
RUN_DIR="$LOG_DIR/run"
DAEMON_LOG="$LOG_DIR/recorder-daemon.log"
PID_FILE="$RUN_DIR/catalog-recorder.pid"
SUPERVISOR_PID="$RUN_DIR/catalog-recorder-supervisor.pid"
UVICORN_LOG="$LOG_DIR/uvicorn.log"
# Auto-rotate uvicorn.log when larger than this many MB (0 = disabled)
LOG_MAX_MB="${LOG_MAX_MB:-80}"

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
export NAUTILUS_LOG_LEVEL="${NAUTILUS_LOG_LEVEL:-WARNING}"

VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing $ROOT/.venv" >&2
  exit 1
fi

_log_mb() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo 0
    return
  fi
  local bytes
  bytes=$(stat -f%z "$path" 2>/dev/null || echo 0)
  echo $((bytes / 1024 / 1024))
}

_prune_junk_logs() {
  rm -f "$LOG_DIR"/*.bak "$LOG_DIR"/uvicorn.log.*.bak 2>/dev/null || true
  rm -f "$LOG_DIR"/market_recorder_*.log "$LOG_DIR"/*_smoke.log 2>/dev/null || true
}

_maybe_rotate_uvicorn() {
  [[ "${LOG_MAX_MB}" -gt 0 ]] || return 0
  local mb
  mb=$(_log_mb "$UVICORN_LOG")
  if [[ "$mb" -lt "${LOG_MAX_MB}" ]]; then
    return 0
  fi
  : >"$UVICORN_LOG"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] rotated uvicorn.log (was ${mb}MB, limit ${LOG_MAX_MB}MB)" >>"$DAEMON_LOG"
}

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
  _maybe_rotate_uvicorn
  export PYTHONPATH="$ROOT"
  PORT="${PORT:-8000}"
  nohup "$VENV_PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
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
    _prune_junk_logs
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
        echo "Logs: tail -f $UVICORN_LOG  (Nautilus level=$NAUTILUS_LOG_LEVEL)"
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
    _prune_junk_logs
    echo "Truncated $UVICORN_LOG and removed .bak / smoke logs"
    ;;
  cleanup-logs)
    _stop_supervisor
    _stop_uvicorn
    _prune_junk_logs
    _maybe_rotate_uvicorn
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
