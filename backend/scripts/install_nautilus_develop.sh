#!/usr/bin/env bash
# Build and install Nautilus develop (≥1.228) with BinanceFuturesLiquidation.
set -euo pipefail

NAUTILUS_ROOT="${NAUTILUS_ROOT:-$HOME/Documents/nautilus_trader}"
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"

export PATH="${HOME}/.cargo/bin:/opt/homebrew/opt/rustup/bin:${PATH}"

if ! command -v rustc >/dev/null; then
  echo "Install Rust 1.96+ (rustup recommended): https://rustup.rs"
  exit 1
fi

echo "rustc: $(rustc --version)"
cd "$NAUTILUS_ROOT"
git fetch https://github.com/nautechsystems/nautilus_trader.git develop 2>/dev/null || true
git merge FETCH_HEAD -m "merge upstream develop" 2>/dev/null || true

source "$BACKEND/.venv/bin/activate"
pip install -q setuptools maturin Cython numpy poetry-core

echo "Building nautilus_trader (release, several minutes)..."
python build.py

pip install -e .
python -c "from nautilus_trader.adapters.binance import BinanceFuturesLiquidation; import nautilus_trader; print('OK', nautilus_trader.__version__, BinanceFuturesLiquidation)"
