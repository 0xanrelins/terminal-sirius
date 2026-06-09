#!/usr/bin/env bash
# Passive health watcher — does not stop or restart the backend.
# Tails uvicorn.log for high-signal errors, watches macOS Python crash reports,
# and optionally pings the FastAPI process. Alerts → backend/logs/health-alerts.log
#
#   cd backend && ./scripts/watch_health.sh
#   cd backend && ./scripts/watch_health.sh --no-http   # skip HTTP probe
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
UVICORN_LOG="$LOG_DIR/uvicorn.log"
ALERT_LOG="$LOG_DIR/health-alerts.log"
CRASH_DIR="${HOME}/Library/Logs/DiagnosticReports"
PORT="${PORT:-8000}"
HTTP_PROBE=1
CRASH_POLL_SEC=30
HTTP_POLL_SEC=60
WATCHER_PIDS=()

UVICORN_PATTERNS='\[ERROR\]|panic|SIGABRT|abort\(|price_precision|catalog flush failed|Unexpected exception|RuntimeError|invalid delta'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-http) HTTP_PROBE=0; shift ;;
    -h|--help)
      echo "Usage: $0 [--no-http]"
      echo "  Watches $UVICORN_LOG and new Python crash reports under $CRASH_DIR"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env"
  set +a
fi
PORT="${PORT:-8000}"

mkdir -p "$LOG_DIR"
touch "$ALERT_LOG" "$UVICORN_LOG"

_ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

_alert() {
  local kind="$1"
  local msg="$2"
  local line="[$(_ts)] [$kind] $msg"
  echo "$line" >>"$ALERT_LOG"
  echo "$line"
}

_cleanup() {
  local pid
  for pid in "${WATCHER_PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap _cleanup EXIT INT TERM

_known_crashes() {
  ls -1 "$CRASH_DIR"/Python-*.ips 2>/dev/null || true
}

_crash_seen() {
  local path="$1"
  local s
  for s in "${CRASH_SEEN[@]:-}"; do
    [[ "$s" == "$path" ]] && return 0
  done
  return 1
}

_alert "START" "watch_health pid=$$ uvicorn_log=$UVICORN_LOG http_probe=$HTTP_PROBE port=$PORT"

# ── uvicorn.log tail ──────────────────────────────────────────────────────────
(
  tail -n 0 -F "$UVICORN_LOG" 2>/dev/null | while IFS= read -r line; do
    if echo "$line" | grep -Eiq "$UVICORN_PATTERNS"; then
      clean=$(printf '%s' "$line" | sed $'s/\x1b\\[[0-9;]*m//g')
      _alert "UVICORN" "${clean:0:500}"
    fi
  done
) &
WATCHER_PIDS+=($!)

# ── macOS Python crash reports ────────────────────────────────────────────────
(
  CRASH_SEEN=()
  while IFS= read -r path; do
    [[ -n "$path" ]] && CRASH_SEEN+=("$path")
  done < <(_known_crashes)
  while true; do
    sleep "$CRASH_POLL_SEC"
    while IFS= read -r path; do
      [[ -z "$path" ]] && continue
      if ! _crash_seen "$path"; then
        CRASH_SEEN+=("$path")
        base=$(basename "$path")
        parent=""
        if grep -q '"coalitionName" : "com.microsoft.VSCode"' "$path" 2>/dev/null; then
          parent=" (VS Code coalition)"
        elif grep -q '"responsibleProc" : "Code"' "$path" 2>/dev/null; then
          parent=" (Code responsible)"
        elif grep -q '"responsibleProc" : "Cursor"' "$path" 2>/dev/null; then
          parent=" (Cursor responsible)"
        fi
        _alert "CRASH" "${base}${parent}"
      fi
    done < <(_known_crashes)
  done
) &
WATCHER_PIDS+=($!)

# ── HTTP liveness (FastAPI up, Nautilus may still be warming) ─────────────────
if [[ "$HTTP_PROBE" -eq 1 ]]; then
  (
    while true; do
      sleep "$HTTP_POLL_SEC"
      if curl -sf --max-time 5 "http://127.0.0.1:${PORT}/openapi.json" >/dev/null; then
        continue
      fi
      if pgrep -f "uvicorn main:app --host 127.0.0.1" >/dev/null 2>&1; then
        _alert "HTTP" "openapi.json unreachable but uvicorn process still running (port $PORT)"
      else
        _alert "HTTP" "backend down — no uvicorn process and openapi.json unreachable (port $PORT)"
      fi
    done
  ) &
  WATCHER_PIDS+=($!)
fi

echo "Watching (Ctrl+C to stop):"
echo "  log:    tail -f $UVICORN_LOG"
echo "  alerts: tail -f $ALERT_LOG"
echo "---"

wait
