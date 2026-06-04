"""Nearest timestamp lookup helpers for native catalog data."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.identifiers import InstrumentId

from catalog import get_catalog
from recorders.binance_liquidation import instrument_symbol
from recorders.second_prices import SecondPrice

T = TypeVar("T")


def _unwrap(row):
    """Catalog queries may return wrapper objects."""
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


def nearest_binance_price(symbol: str, ts_ns: int, window_seconds: int = 120) -> SecondPrice | None:
    """Nearest Binance perp last trade price from native ``TradeTick`` catalog rows."""
    catalog = get_catalog()
    span = window_seconds * 1_000_000_000
    iid = InstrumentId.from_str(symbol)
    rows = catalog.query(
        data_cls=TradeTick,
        identifiers=[str(iid)],
        start=ts_ns - span,
        end=ts_ns + span,
    )
    ticks = [_unwrap(row) for row in rows if isinstance(_unwrap(row), TradeTick)]
    tick = _nearest_by_ts(ticks, ts_ns=ts_ns)
    if tick is None:
        return None
    return SecondPrice(
        ts_event=int(tick.ts_event),
        symbol=symbol,
        last_price=float(tick.price.as_double()),
    )


def nearest_polymarket_price(instrument_id: str, ts_ns: int, window_seconds: int = 120) -> SecondPrice | None:
    """Nearest Polymarket mid price from native ``QuoteTick`` catalog rows."""
    catalog = get_catalog()
    span = window_seconds * 1_000_000_000
    iid = InstrumentId.from_str(instrument_id)
    rows = catalog.query(
        data_cls=QuoteTick,
        identifiers=[str(iid)],
        start=ts_ns - span,
        end=ts_ns + span,
    )
    quotes = [_unwrap(row) for row in rows if isinstance(_unwrap(row), QuoteTick)]
    quote = _nearest_by_ts(quotes, ts_ns=ts_ns)
    if quote is None:
        return None
    bid = float(quote.bid_price.as_double())
    ask = float(quote.ask_price.as_double())
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)
    return SecondPrice(
        ts_event=int(quote.ts_event),
        symbol=instrument_id,
        last_price=mid,
    )


def nearest_price_for_liquidation(
    liquidation,
    window_seconds: int = 120,
) -> SecondPrice | None:
    return nearest_binance_price(
        symbol=instrument_symbol(liquidation),
        ts_ns=int(liquidation.ts_event),
        window_seconds=window_seconds,
    )
