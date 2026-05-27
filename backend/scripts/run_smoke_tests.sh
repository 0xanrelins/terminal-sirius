#!/usr/bin/env bash
# Offline regression suite (no network, no pytest required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"

exec python3 "$ROOT/scripts/run_smoke_tests.py"
