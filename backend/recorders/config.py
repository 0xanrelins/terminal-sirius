"""Configuration helpers for the Nautilus market recorder runtime."""
from __future__ import annotations

import os

DEFAULT_BINANCE_INSTRUMENTS: tuple[str, ...] = (
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
    "XRPUSDT-PERP.BINANCE",
    "DOGEUSDT-PERP.BINANCE",
    "HYPEUSDT-PERP.BINANCE",
)

DEFAULT_POLYMARKET_SERIES: tuple[str, ...] = (
    "btc-updown-15m",
    "eth-updown-15m",
    "sol-updown-15m",
    "xrp-updown-15m",
    "doge-updown-15m",
    "hype-updown-15m",
)

DEFAULT_FLUSH_INTERVAL_MS = 1_000
DEFAULT_MAX_BATCH_ROWS = 5_000


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def binance_instruments_from_env() -> tuple[str, ...]:
    raw = os.environ.get("RECORDER_BINANCE_INSTRUMENTS", "")
    return _split_csv(raw) or DEFAULT_BINANCE_INSTRUMENTS


def polymarket_series_from_env() -> tuple[str, ...]:
    raw = os.environ.get("RECORDER_POLYMARKET_SERIES", "")
    return _split_csv(raw) or DEFAULT_POLYMARKET_SERIES


def flush_interval_ms_from_env() -> int:
    raw = os.environ.get("RECORDER_FLUSH_INTERVAL_MS")
    if not raw:
        return DEFAULT_FLUSH_INTERVAL_MS
    return max(100, int(raw))


def max_batch_rows_from_env() -> int:
    raw = os.environ.get("RECORDER_MAX_BATCH_ROWS")
    if not raw:
        return DEFAULT_MAX_BATCH_ROWS
    return max(100, int(raw))


def is_enabled() -> bool:
    """Parquet market recorder on the shared TradingNode (default on)."""
    raw = os.environ.get("MARKET_RECORDER_ENABLED", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")
