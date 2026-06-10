"""Derive per-second last prices from native ``TradeTick`` catalog rows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId


@dataclass(frozen=True)
class SecondPrice:
    """Last trade price within a one-second bucket (Nautilus ``ts_event`` at second boundary)."""

    ts_event: int
    symbol: str
    last_price: float


@dataclass(frozen=True)
class SymbolPriceSeries:
    rows: tuple[SecondPrice, ...]
    times_ns: tuple[int, ...]


def _unwrap(row: Any) -> Any:
    return getattr(row, "data", row)


def ticks_to_second_prices(ticks: list[TradeTick], *, symbol: str) -> list[SecondPrice]:
    """Collapse trade ticks to one row per second (last price wins)."""
    if not ticks:
        return []
    by_sec: dict[int, float] = {}
    for tick in sorted(ticks, key=lambda t: int(t.ts_event)):
        sec = int(tick.ts_event) // 1_000_000_000
        by_sec[sec] = float(tick.price.as_double())
    return [
        SecondPrice(
            ts_event=sec * 1_000_000_000,
            symbol=symbol,
            last_price=price,
        )
        for sec, price in sorted(by_sec.items())
    ]


def load_event_second_prices(
    catalog: Any,
    symbol: str,
    event_ts_ns: int,
    window_sec: int,
    *,
    lookback_sec: int = 120,
) -> SymbolPriceSeries:
    """Per-event TradeTick window (aligned anchor + post-liq path for catalog replay)."""
    start_ns = max(0, event_ts_ns - lookback_sec * 1_000_000_000)
    end_ns = event_ts_ns + window_sec * 1_000_000_000
    iid = InstrumentId.from_str(symbol)
    raw_ticks = catalog.query(
        data_cls=TradeTick,
        identifiers=[str(iid)],
        start=start_ns,
        end=end_ns,
    )
    ticks = [t for raw in raw_ticks if isinstance(t := _unwrap(raw), TradeTick)]
    rows = ticks_to_second_prices(ticks, symbol=symbol)
    return SymbolPriceSeries(
        rows=tuple(rows),
        times_ns=tuple(int(r.ts_event) for r in rows),
    )


def load_second_prices_by_symbol(
    catalog: Any,
    symbols: set[str],
    start_ns: int,
    end_ns: int,
) -> dict[str, SymbolPriceSeries]:
    """Load ``TradeTick`` rows and aggregate to per-second prices per symbol."""
    out: dict[str, SymbolPriceSeries] = {}
    for sym in symbols:
        iid = InstrumentId.from_str(sym)
        raw_ticks = catalog.query(
            data_cls=TradeTick,
            identifiers=[str(iid)],
            start=start_ns,
            end=end_ns,
        )
        ticks = [t for raw in raw_ticks if isinstance(t := _unwrap(raw), TradeTick)]
        rows = ticks_to_second_prices(ticks, symbol=sym)
        out[sym] = SymbolPriceSeries(
            rows=tuple(rows),
            times_ns=tuple(int(r.ts_event) for r in rows),
        )
    return out
