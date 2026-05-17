"""Simulation strategy configuration."""
from __future__ import annotations

import json
import os
from typing import Literal

Side = Literal["long", "short"]
SIDES: tuple[Side, ...] = ("long", "short")

from adapters.polymarket.rolling import WINDOW_SEC

# Binance perp symbol → Polymarket 15m series
ASSETS: dict[str, dict[str, str]] = {
    "BTC": {
        "binance_symbol": "BTCUSDT-PERP.BINANCE",
        "poly_series": "btc-updown-15m",
    },
    "ETH": {
        "binance_symbol": "ETHUSDT-PERP.BINANCE",
        "poly_series": "eth-updown-15m",
    },
    "SOL": {
        "binance_symbol": "SOLUSDT-PERP.BINANCE",
        "poly_series": "sol-updown-15m",
    },
    "XRP": {
        "binance_symbol": "XRPUSDT-PERP.BINANCE",
        "poly_series": "xrp-updown-15m",
    },
    "DOGE": {
        "binance_symbol": "DOGEUSDT-PERP.BINANCE",
        "poly_series": "doge-updown-15m",
    },
}

BINANCE_TO_ASSET: dict[str, str] = {
    v["binance_symbol"]: k for k, v in ASSETS.items()
}

SERIES_TO_ASSET: dict[str, str] = {
    v["poly_series"]: k for k, v in ASSETS.items()
}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "BTC": 100_000,
    "ETH": 50_000,
    "SOL": 50_000,
    "XRP": 25_000,
    "DOGE": 25_000,
}


def is_enabled() -> bool:
    return os.environ.get("SIM_ENABLED", "true").lower() in ("1", "true", "yes")


def min_usd() -> float:
    return float(os.environ.get("SIM_MIN_USD", "1.0"))


def min_shares_default() -> float:
    return float(os.environ.get("SIM_MIN_SHARES", "5"))


def thresholds() -> dict[str, float]:
    raw = os.environ.get("SIM_THRESHOLDS_JSON")
    if raw:
        try:
            data = json.loads(raw)
            return {k.upper(): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return dict(DEFAULT_THRESHOLDS)


def next_window_open(ts_sec: int) -> int:
    """First 15m window open strictly after signal time."""
    return (ts_sec // WINDOW_SEC + 1) * WINDOW_SEC
