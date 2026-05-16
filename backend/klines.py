"""
Historical klines with DB-first strategy:

1. Query PostgreSQL — if we have enough bars and the tail is fresh, return them.
2. If the tail is stale, fetch recent bars from Binance, merge, persist, return.
3. If coverage is low, full Binance fetch (pagination uses `before` without tail refresh).
"""
import time

import httpx

import db
from bar_time import INTERVAL_SECONDS, bar_open_time, is_aligned_open_time

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

def _nautilus_to_binance_symbol(symbol: str) -> str:
    """'BTCUSDT-PERP.BINANCE' -> 'BTCUSDT'"""
    base = symbol.split(".")[0]
    base = base.replace("-PERP", "")
    return base


def _current_bar_open(interval: str) -> int:
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    now = int(time.time())
    return (now // bar_sec) * bar_sec


def _is_tail_stale(rows: list[dict], interval: str) -> bool:
    if not rows:
        return True
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    return time.time() - rows[-1]["time"] > bar_sec * 2


def _is_missing_forming_bar(rows: list[dict], interval: str) -> bool:
    if not rows:
        return True
    return rows[-1]["time"] < _current_bar_open(interval)


def _merge_klines(interval: str, *groups: list[dict]) -> list[dict]:
    """Merge rows; normalize timestamps to bar open; combine duplicate buckets."""
    by_time: dict[int, dict] = {}
    for group in groups:
        for row in group:
            t = bar_open_time(int(row["time"]), interval)
            norm = {**row, "time": t}
            prev = by_time.get(t)
            if prev is None:
                by_time[t] = norm
                continue
            by_time[t] = {
                "time": t,
                "open": prev["open"],
                "high": max(prev["high"], norm["high"]),
                "low": min(prev["low"], norm["low"]),
                "close": norm["close"],
                "volume": max(prev.get("volume", 0), norm.get("volume", 0)),
            }
    return [by_time[t] for t in sorted(by_time)]


def _normalize_series(rows: list[dict], interval: str) -> list[dict]:
    if not rows:
        return rows
    return _merge_klines(interval, rows)


def _has_misaligned_times(rows: list[dict], interval: str) -> bool:
    """Detect close-time bars persisted before open-time normalization."""
    if not rows:
        return False
    tail = rows[-min(30, len(rows)) :]
    return any(not is_aligned_open_time(int(r["time"]), interval) for r in tail)


def _has_internal_gap(rows: list[dict], interval: str) -> bool:
    if len(rows) < 2:
        return False
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    max_step = int(bar_sec * 1.5)
    for i in range(1, len(rows)):
        if rows[i]["time"] - rows[i - 1]["time"] > max_step:
            return True
    return False


def _needs_full_refresh(rows: list[dict], interval: str, limit: int) -> bool:
    if len(rows) < int(limit * 0.8):
        return True
    return (
        _is_tail_stale(rows, interval)
        or _has_internal_gap(rows, interval)
        or _has_misaligned_times(rows, interval)
    )


async def fetch_klines(
    symbol: str, interval: str = "1m", limit: int = 500, before: int | None = None
) -> list[dict]:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Invalid interval: {interval!r}")

    cached = await db.get_klines(symbol, interval, limit, before=before)

    # Older pages: DB-first when sufficiently populated
    if before is not None:
        if len(cached) >= int(limit * 0.8):
            return _normalize_series(cached, interval)
        fresh = await _fetch_from_binance(symbol, interval, limit, before=before)
        await db.upsert_klines(symbol, interval, fresh)
        return fresh

    # Latest window: full refresh when sparse, stale, or gapped
    if _needs_full_refresh(cached, interval, limit):
        fresh = await _fetch_from_binance(symbol, interval, limit)
        await db.upsert_klines(symbol, interval, fresh)
        return fresh

    # Append in-progress bar from Binance (DB often ends at last closed candle)
    if _is_missing_forming_bar(cached, interval):
        tail = await _fetch_from_binance(symbol, interval, 3)
        merged = _merge_klines(interval, cached, tail)[-limit:]
        await db.upsert_klines(symbol, interval, tail)
        return merged

    return _normalize_series(cached, interval)


async def _fetch_from_binance(
    symbol: str, interval: str, limit: int, before: int | None = None
) -> list[dict]:
    binance_symbol = _nautilus_to_binance_symbol(symbol)
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
    params: dict = {"symbol": binance_symbol, "interval": interval, "limit": limit}
    if before is not None:
        params["endTime"] = before * 1000 - 1  # exclusive upper bound (seconds → ms)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()

    return [
        {
            "time": row[0] // 1000,   # ms → unix seconds
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in raw
    ]
