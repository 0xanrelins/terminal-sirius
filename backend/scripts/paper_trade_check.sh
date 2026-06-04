#!/usr/bin/env bash
# Paper trade ön kontrol — secrets yazdırmaz.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/.." && pwd)"
cd "$ROOT"

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[0;33m'
RST='\033[0m'

ok=0
warn=0
fail=0

_pass() { echo -e "${GRN}OK${RST}  $1"; ok=$((ok + 1)); }
_warn() { echo -e "${YLW}WARN${RST} $1"; warn=$((warn + 1)); }
_fail() { echo -e "${RED}FAIL${RST} $1"; fail=$((fail + 1)); }

_env() {
  local name="$1"
  if [[ -f "$REPO_ROOT/.env" ]]; then
    grep -E "^${name}=" "$REPO_ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true
  fi
}

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$REPO_ROOT/.env"
  set +a
  _pass ".env loaded"
else
  _fail "Missing $REPO_ROOT/.env"
fi

if docker compose -f "$REPO_ROOT/docker-compose.yml" ps postgres 2>/dev/null | grep -q healthy; then
  _pass "Postgres healthy"
elif docker compose -f "$REPO_ROOT/docker-compose.yml" ps postgres 2>/dev/null | grep -q Up; then
  _warn "Postgres up (not healthy yet)"
else
  _fail "Postgres not running — docker compose up -d postgres"
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  _pass "Python venv"
else
  _fail "Missing backend/.venv"
fi

_is_truthy() {
  case "$(echo "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

se="${STRATEGY_ENABLED:-false}"
if _is_truthy "$se"; then
  _pass "STRATEGY_ENABLED=true"
else
  _fail "STRATEGY_ENABLED not true — set in .env for paper trade"
fi

pt="${STRATEGY_PAPER_TRADE:-true}"
if _is_truthy "$pt"; then
  _pass "STRATEGY_PAPER_TRADE=true (Sandbox)"
else
  _warn "STRATEGY_PAPER_TRADE=false — live exec path"
fi

exec_enabled="${POLYMARKET_EXEC_ENABLED:-false}"
if _is_truthy "$exec_enabled" && _is_truthy "$pt"; then
  _warn "POLYMARKET_EXEC_ENABLED=true with paper — Sandbox wins when STRATEGY_PAPER_TRADE=true"
fi

ll="${NAUTILUS_LOG_LEVEL:-WARNING}"
case "$(echo "$ll" | tr '[:lower:]' '[:upper:]')" in
  INFO|DEBUG) _pass "NAUTILUS_LOG_LEVEL=$ll (PAPER logs visible)" ;;
  *) _warn "NAUTILUS_LOG_LEVEL=$ll — set INFO to see PAPER fill/position in uvicorn.log" ;;
esac

echo ""
echo "Liq thresholds (.env):"
for coin in BTC ETH SOL XRP DOGE; do
  key="LIQ_THRESHOLD_${coin}"
  val="${!key:-}"
  if [[ -n "$val" ]]; then
    echo "  $key=$val"
  else
    echo "  $key=(default)"
  fi
done

if [[ -f "$ROOT/logs/uvicorn.log" ]]; then
  if grep -q "TerminalSiriusStrategy + signal actors enabled" "$ROOT/logs/uvicorn.log" 2>/dev/null; then
    _pass "Strategy registered in uvicorn.log"
  else
    _warn "Strategy not in log yet — restart backend after .env change"
  fi
  if grep -q "Polymarket Sandbox execution" "$ROOT/logs/uvicorn.log" 2>/dev/null; then
    _pass "Sandbox execution in log"
  fi
  paper_hits=0
  if grep -q "PAPER " "$ROOT/logs/uvicorn.log" 2>/dev/null; then
    paper_hits=$(grep -c "PAPER " "$ROOT/logs/uvicorn.log" 2>/dev/null | head -1 || echo 0)
  fi
  if [[ "${paper_hits:-0}" -gt 0 ]]; then
    _pass "PAPER events in log: $paper_hits"
  else
    _warn "No PAPER events yet (warm-up ~900s or no aligned signal)"
  fi
fi

echo ""
echo "--- Summary: $ok ok, $warn warn, $fail fail ---"
[[ "$fail" -eq 0 ]]
