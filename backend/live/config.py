"""Live trading strategy configuration."""
from __future__ import annotations

import json
import os
from typing import Literal

from adapters.polymarket.rolling import WINDOW_SEC

Side = Literal["long", "short"]
SIDES: tuple[Side, ...] = ("long", "short")

ASSETS: dict[str, dict[str, str]] = {
    "SOL": {
        "binance_symbol": "SOLUSDT-PERP.BINANCE",
        "poly_series": "sol-updown-15m",
    },
    "DOGE": {
        "binance_symbol": "DOGEUSDT-PERP.BINANCE",
        "poly_series": "doge-updown-15m",
    },
}

BINANCE_TO_ASSET: dict[str, str] = {
    v["binance_symbol"]: k for k, v in ASSETS.items()
}

SERIES_TO_ASSET: dict[str, str] = {v["poly_series"]: k for k, v in ASSETS.items()}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "SOL": 200_000,
    "DOGE": 200_000,
}


def is_enabled() -> bool:
    return os.environ.get("LIVE_ENABLED", "true").lower() in ("1", "true", "yes")


def min_usd() -> float:
    return float(os.environ.get("LIVE_MIN_USD", "1.0"))


def min_shares_default() -> float:
    return float(os.environ.get("LIVE_MIN_SHARES", "5"))


def thresholds() -> dict[str, float]:
    raw = os.environ.get("LIVE_THRESHOLDS_JSON")
    if raw:
        try:
            data = json.loads(raw)
            return {k.upper(): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return dict(DEFAULT_THRESHOLDS)


def active_assets() -> dict[str, dict[str, str]]:
    raw = os.environ.get("LIVE_ASSETS", "SOL,DOGE")
    keys = {k.strip().upper() for k in raw.split(",") if k.strip()}
    if not keys:
        return dict(ASSETS)
    return {k: v for k, v in ASSETS.items() if k in keys}


def next_window_open(ts_sec: int) -> int:
    return (ts_sec // WINDOW_SEC + 1) * WINDOW_SEC


def bet_window_open(liq_bar_open: int) -> int:
    """Polymarket window right after the 15m liq bar (anchored to bar, not wall clock)."""
    return liq_bar_open + WINDOW_SEC
