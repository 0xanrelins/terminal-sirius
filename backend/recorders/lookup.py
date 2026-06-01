"""Nearest timestamp lookup helpers for recorder parquet data."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from catalog import get_catalog
from recorders.data_types import (
    BinanceLiquidationEvent,
    BinanceSecondPrice,
    PolymarketSecondPrice,
)

T = TypeVar("T")


def _unwrap(row):
    """Catalog custom-data queries may return CustomData wrappers."""
    return getattr(row, "data", row)


def _nearest_by_ts(items: Iterable[T], *, ts_ns: int) -> T | None:
    best: T | None = None
    best_dist: int | None = None
    for item in items:
        dist = abs(int(getattr(item, "ts_event")) - ts_ns)
        if best_dist is None or dist < best_dist:
            best = item
            best_dist = dist
    return best


def nearest_binance_price(symbol: str, ts_ns: int, window_seconds: int = 120) -> BinanceSecondPrice | None:
    catalog = get_catalog()
    span = window_seconds * 1_000_000_000
    rows = catalog.query(
        data_cls=BinanceSecondPrice,
        start=ts_ns - span,
        end=ts_ns + span,
    )
    filtered = [_unwrap(row) for row in rows if _unwrap(row).symbol == symbol]
    return _nearest_by_ts(filtered, ts_ns=ts_ns)


def nearest_polymarket_price(market: str, ts_ns: int, window_seconds: int = 120) -> PolymarketSecondPrice | None:
    catalog = get_catalog()
    span = window_seconds * 1_000_000_000
    rows = catalog.query(
        data_cls=PolymarketSecondPrice,
        start=ts_ns - span,
        end=ts_ns + span,
    )
    filtered = [_unwrap(row) for row in rows if _unwrap(row).market == market]
    return _nearest_by_ts(filtered, ts_ns=ts_ns)


def nearest_price_for_liquidation(
    liquidation: BinanceLiquidationEvent,
    window_seconds: int = 120,
) -> BinanceSecondPrice | None:
    return nearest_binance_price(
        symbol=liquidation.symbol,
        ts_ns=int(liquidation.ts_event),
        window_seconds=window_seconds,
    )
