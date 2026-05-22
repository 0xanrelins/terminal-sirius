"""Live trading strategy configuration."""
from __future__ import annotations

import json
import os
from typing import Literal

from adapters.polymarket.rolling import WINDOW_SEC
from simulation.config import ASSETS, DEFAULT_THRESHOLDS
from strategy_env import resolve_active_keys

Side = Literal["long", "short"]
SIDES: tuple[Side, ...] = ("long", "short")

BINANCE_TO_ASSET: dict[str, str] = {
    v["binance_symbol"]: k for k, v in ASSETS.items()
}

SERIES_TO_ASSET: dict[str, str] = {v["poly_series"]: k for k, v in ASSETS.items()}


def is_enabled() -> bool:
    return os.environ.get("LIVE_ENABLED", "true").lower() in ("1", "true", "yes")


def min_usd() -> float:
    return float(os.environ.get("LIVE_MIN_USD", "1.0"))


def min_shares_default() -> float:
    return float(os.environ.get("LIVE_MIN_SHARES", "5"))


def active_asset_keys() -> set[str]:
    return resolve_active_keys(
        catalog=set(ASSETS.keys()),
        csv_env="LIVE_ASSETS",
        thresholds_env="LIVE_THRESHOLDS_JSON",
    )


def active_assets() -> dict[str, dict[str, str]]:
    keys = active_asset_keys()
    return {k: v for k, v in ASSETS.items() if k in keys}


def _parsed_thresholds_json() -> dict[str, float]:
    raw = os.environ.get("LIVE_THRESHOLDS_JSON")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k).upper(): float(v) for k, v in data.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def thresholds() -> dict[str, float]:
    keys = active_asset_keys()
    parsed = _parsed_thresholds_json()
    out: dict[str, float] = {}
    for k in keys:
        if k in parsed:
            out[k] = parsed[k]
        elif k in DEFAULT_THRESHOLDS:
            out[k] = DEFAULT_THRESHOLDS[k]
    return out


def next_window_open(ts_sec: int) -> int:
    return (ts_sec // WINDOW_SEC + 1) * WINDOW_SEC


def bet_window_open(liq_bar_open: int) -> int:
    """Polymarket window right after the 15m liq bar (anchored to bar, not wall clock)."""
    return liq_bar_open + WINDOW_SEC
