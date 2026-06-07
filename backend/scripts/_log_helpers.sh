# Shared log rotation / cleanup for run_backend.sh and run_catalog_recorder_daemon.sh
# shellcheck shell=bash

LOG_MAX_MB="${LOG_MAX_MB:-80}"

_log_mb() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo 0
    return
  fi
  local bytes
  bytes=$(stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null || echo 0)
  echo $((bytes / 1024 / 1024))
}

_prune_junk_logs() {
  local log_dir="$1"
  rm -f "$log_dir"/*.bak "$log_dir"/uvicorn.log.*.bak 2>/dev/null || true
  rm -f "$log_dir"/market_recorder_*.log "$log_dir"/*_smoke.log 2>/dev/null || true
  rm -f "$log_dir"/smoke.log "$log_dir"/uvicorn_verify.log 2>/dev/null || true
}

# Truncate log_path when it exceeds LOG_MAX_MB. Optional note_path receives a rotation line.
_maybe_rotate_log() {
  local log_path="$1"
  local note_path="${2:-}"
  [[ "${LOG_MAX_MB}" -gt 0 ]] || return 0
  local mb
  mb=$(_log_mb "$log_path")
  if [[ "$mb" -lt "${LOG_MAX_MB}" ]]; then
    return 0
  fi
  : >"$log_path"
  if [[ -n "$note_path" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] rotated $(basename "$log_path") (was ${mb}MB, limit ${LOG_MAX_MB}MB)" >>"$note_path"
  fi
}

UVICORN_LOG_ARGS=(--no-access-log --log-level warning)
