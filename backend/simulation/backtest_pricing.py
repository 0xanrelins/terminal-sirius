"""Paper entry prices for BacktestEngine (no live Nautilus quote stream)."""
from __future__ import annotations

from simulation.config import Side

# Neutral default when catalog has no Polymarket quote history for a window.
DEFAULT_BACKTEST_ENTRY = 0.50


def backtest_entry_for_side(side: Side, *, yes_mid: float | None = None) -> float:
    if yes_mid is not None and 0.01 < yes_mid < 0.99:
        return yes_mid if side == "long" else (1.0 - yes_mid)
    return DEFAULT_BACKTEST_ENTRY
