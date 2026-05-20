"""
Binance USDT-M liquidation buckets (long / short notional per interval bar).

Long liquidation  = force order side SELL (long position closed)
Short liquidation = force order side BUY  (short position closed)

Raw forceOrder items → PostgreSQL (liquidation_events).
Bar aggregates → liquidation_bars.
"""
from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Any

import db

MAJOR_NAUTILUS_SYMBOLS: frozenset[str] = frozenset({
    "BTCUSDT-PERP.BINANCE",
    "ETHUSDT-PERP.BINANCE",
    "SOLUSDT-PERP.BINANCE",
    "DOGEUSDT-PERP.BINANCE",
    "XRPUSDT-PERP.BINANCE",
})

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


def force_order_trade_id(item: dict[str, Any]) -> int:
    """Stable dedupe key from Binance forceOrder item."""
    order = item.get("o") or {}
    if order.get("i") is not None:
        return int(order["i"])
    sym = str(order.get("s", ""))
    side = str(order.get("S", ""))
    trade_ms = int(order.get("T", 0))
    sym_tag = sum(ord(c) for c in sym) % 10_000
    side_tag = 1 if side == "SELL" else 2
    return trade_ms * 10_000 + sym_tag * 10 + side_tag


def parse_force_order(item: dict[str, Any]) -> dict[str, Any] | None:
    """Raw Binance forceOrder item → slim liquidation fields."""
    if item.get("e") != "forceOrder":
        return None
    order = item.get("o")
    if not order:
        return None
    try:
        symbol = binance_to_nautilus(str(order["s"]))
        side = str(order["S"])
        notional = float(order["ap"]) * float(order["z"])
        trade_ms = int(order["T"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "type": "liquidation",
        "trade_id": force_order_trade_id(item),
        "symbol": symbol,
        "side": side,
        "notional": round(notional, 2),
        "time": trade_ms // 1000,
    }


def build_liquidation_message(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse raw item and attach bar-bucket deltas for persistence."""
    parsed = parse_force_order(item)
    if parsed is None:
        return None
    trade_ms = int((item.get("o") or {})["T"])
    updates = record_liquidation(
        parsed["symbol"], parsed["side"], parsed["notional"], trade_ms
    )
    bar_snapshots: list[dict[str, Any]] = []
    with _lock:
        for u in updates:
            bucket = _buckets[(u["symbol"], u["interval"], u["time"])]
            bar_snapshots.append({
                "interval": u["interval"],
                "time": u["time"],
                "long": round(bucket["long"], 2),
                "short": round(bucket["short"], 2),
            })
    return {
        **parsed,
        "bars": bar_snapshots,
        "_payload": item,
        "_updates": updates,
    }


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


async def fetch_liquidation_events(
    symbols: tuple[str, ...] | None = None,
    limit: int = 200,
) -> list[dict]:
    """Load recent raw events from DB, parse, filter by symbol set."""
    sym_set = set(symbols) if symbols else MAJOR_NAUTILUS_SYMBOLS
    cap = min(max(limit, 1), 500)
    # Over-fetch so filtering majors still fills the limit.
    payloads = await db.get_liquidation_event_payloads(cap * 20)
    out: list[dict] = []
    for payload in payloads:
        parsed = parse_force_order(payload)
        if parsed is None or parsed["symbol"] not in sym_set:
            continue
        out.append({k: v for k, v in parsed.items() if not k.startswith("_")})
        if len(out) >= cap:
            break
    return out
