"""
Historical klines with DB-first strategy:

1. Query PostgreSQL — if we have ≥80% of the requested bars, return them directly.
2. Otherwise fall back to Binance Futures public REST, store the result, then return.

This means the first request for a symbol/interval hits Binance; every subsequent
request (after the Nautilus node has been filling in live bars) is served from DB.
"""
import httpx

import db

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


async def fetch_klines(symbol: str, interval: str = "1m", limit: int = 500) -> list[dict]:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Invalid interval: {interval!r}")

    # 1. Try PostgreSQL
    cached = await db.get_klines(symbol, interval, limit)
    if len(cached) >= int(limit * 0.8):
        return cached

    # 2. Fall back to Binance REST
    fresh = await _fetch_from_binance(symbol, interval, limit)

    # 3. Persist for next time
    await db.upsert_klines(symbol, interval, fresh)

    return fresh


async def _fetch_from_binance(symbol: str, interval: str, limit: int) -> list[dict]:
    binance_symbol = _nautilus_to_binance_symbol(symbol)
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params={"symbol": binance_symbol, "interval": interval, "limit": limit})
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
