"""
Binance USDT-M liquidation buckets (long / short notional per interval bar).

Long liquidation  = force order side SELL (long position closed)
Short liquidation = force order side BUY  (short position closed)

Persisted to PostgreSQL (liquidation_bars); in-memory cache for live overlay.
"""
from __future__ import annotations

from collections import defaultdict
from threading import Lock

import db

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400,
}

_buckets: dict[tuple[str, str, int], dict[str, float]] = defaultdict(
    lambda: {"long": 0.0, "short": 0.0}
)
_lock = Lock()


def nautilus_to_binance(symbol: str) -> str:
    base = symbol.split(".")[0].replace("-PERP", "")
    return base.upper()


def binance_to_nautilus(binance_symbol: str) -> str:
    return f"{binance_symbol.upper()}-PERP.BINANCE"


def bucket_time(ts_ms: int, interval: str) -> int:
    sec = INTERVAL_SECONDS.get(interval, 60)
    ts_s = ts_ms // 1000
    return (ts_s // sec) * sec


def record_liquidation(
    nautilus_symbol: str, side: str, notional_usd: float, trade_time_ms: int
) -> list[dict]:
    """
    Aggregate into in-memory buckets; return per-interval deltas for DB persist.
    """
    long_delta = notional_usd if side == "SELL" else 0.0
    short_delta = notional_usd if side == "BUY" else 0.0
    updates: list[dict] = []

    with _lock:
        for interval in INTERVAL_SECONDS:
            t = bucket_time(trade_time_ms, interval)
            key = (nautilus_symbol, interval, t)
            bucket = _buckets[key]
            bucket["long"] += long_delta
            bucket["short"] += short_delta
            updates.append({
                "symbol": nautilus_symbol,
                "interval": interval,
                "time": t,
                "long_delta": long_delta,
                "short_delta": short_delta,
            })
    return updates


def get_memory_bars(
    symbol: str, interval: str, limit: int, before: int | None = None
) -> list[dict]:
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"Invalid interval: {interval!r}")

    with _lock:
        items = [
            (t, b["long"], b["short"])
            for (sym, iv, t), b in _buckets.items()
            if sym == symbol and iv == interval and (before is None or t < before)
        ]
    items.sort(key=lambda x: x[0])
    if len(items) > limit:
        items = items[-limit:]
    return [
        {"time": t, "long": round(long_v, 2), "short": round(short_v, 2)}
        for t, long_v, short_v in items
    ]


def _merge_db_and_memory(db_bars: list[dict], mem_bars: list[dict]) -> list[dict]:
    """Union by time; take max long/short (memory may be ahead of async persist)."""
    by_time: dict[int, dict[str, float]] = {}
    for bar in db_bars:
        by_time[bar["time"]] = {"long": bar["long"], "short": bar["short"]}
    for bar in mem_bars:
        cur = by_time.get(bar["time"], {"long": 0.0, "short": 0.0})
        by_time[bar["time"]] = {
            "long": max(cur["long"], bar["long"]),
            "short": max(cur["short"], bar["short"]),
        }
    return [
        {"time": t, "long": round(v["long"], 2), "short": round(v["short"], 2)}
        for t, v in sorted(by_time.items())
    ]


async def fetch_liquidation_bars(
    symbol: str, interval: str, limit: int, before: int | None = None
) -> list[dict]:
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"Invalid interval: {interval!r}")

    db_bars = await db.get_liquidation_bars(symbol, interval, limit, before=before)
    mem_bars = get_memory_bars(symbol, interval, limit, before=before)
    merged = _merge_db_and_memory(db_bars, mem_bars)
    if len(merged) > limit:
        merged = merged[-limit:]
    return merged
