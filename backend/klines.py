"""
Historical klines with DB-first strategy:

1. Query PostgreSQL — if we have enough bars and the tail is fresh, return them.
2. If the tail is stale, fetch recent bars from Binance, merge, persist, return.
3. If coverage is low, full Binance fetch (pagination uses `before` without tail refresh).
"""
import time

import httpx

import db

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259_200,
    "1w": 604_800,
}


def _nautilus_to_binance_symbol(symbol: str) -> str:
    """'BTCUSDT-PERP.BINANCE' -> 'BTCUSDT'"""
    base = symbol.split(".")[0]
    base = base.replace("-PERP", "")
    return base


def _is_tail_stale(rows: list[dict], interval: str) -> bool:
    if not rows:
        return True
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    return time.time() - rows[-1]["time"] > bar_sec * 2


def _has_internal_gap(rows: list[dict], interval: str) -> bool:
    if len(rows) < 2:
        return False
    bar_sec = INTERVAL_SECONDS.get(interval, 60)
    max_step = int(bar_sec * 1.5)
    for i in range(1, len(rows)):
        if rows[i]["time"] - rows[i - 1]["time"] > max_step:
            return True
    return False


def _needs_refresh(rows: list[dict], interval: str, limit: int) -> bool:
    if len(rows) < int(limit * 0.8):
        return True
    return _is_tail_stale(rows, interval) or _has_internal_gap(rows, interval)


async def fetch_klines(
    symbol: str, interval: str = "1m", limit: int = 500, before: int | None = None
) -> list[dict]:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Invalid interval: {interval!r}")

    cached = await db.get_klines(symbol, interval, limit, before=before)

    # Older pages: DB-first when sufficiently populated
    if before is not None:
        if len(cached) >= int(limit * 0.8):
            return cached
        fresh = await _fetch_from_binance(symbol, interval, limit, before=before)
        await db.upsert_klines(symbol, interval, fresh)
        return fresh

  # Latest window: Binance returns a contiguous series (required by LWC setData)
    if _needs_refresh(cached, interval, limit):
        fresh = await _fetch_from_binance(symbol, interval, limit)
        await db.upsert_klines(symbol, interval, fresh)
        return fresh

    return cached


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
