"""
Prepare ParquetDataCatalog for ``BacktestNode`` replay.

- Binance perps: ``BinanceFuturesInstrumentProvider`` (public exchange info).
- Polymarket rolling markets: ``PolymarketDataLoader`` per 15m slug in range.
- ``ActivePolymarketMarket`` events written for strategy discovery replay.
- Trade/quote ticks re-written with ``instrument.make_price`` / ``make_qty`` so
  precision matches the instrument (backtest engine requires exact match).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
from nautilus_trader.adapters.binance.factories import get_cached_binance_http_client
from nautilus_trader.adapters.binance.futures.providers import BinanceFuturesInstrumentProvider
from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader
from nautilus_trader.common.component import LiveClock
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.rolling import WINDOW_SEC, slug_for_series
from strategies.mapping import BINANCE_TO_POLY_SERIES, STRATEGY_BINANCE_INSTRUMENTS

if TYPE_CHECKING:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog


def _window_starts_sec(start_ns: int | None, end_ns: int | None) -> list[int]:
    if start_ns is None or end_ns is None:
        return []
    start_s = int(start_ns // 1_000_000_000)
    end_s = int(end_ns // 1_000_000_000)
    first = (start_s // WINDOW_SEC) * WINDOW_SEC
    last = (end_s // WINDOW_SEC) * WINDOW_SEC
    return list(range(first, last + WINDOW_SEC, WINDOW_SEC))


async def _load_binance_perps(instrument_ids: tuple[str, ...]) -> list:
    clock = LiveClock()
    client = get_cached_binance_http_client(
        clock=clock,
        account_type=BinanceAccountType.USDT_FUTURES,
        api_key=None,
        api_secret=None,
    )
    provider = BinanceFuturesInstrumentProvider(
        client=client,
        clock=clock,
        account_type=BinanceAccountType.USDT_FUTURES,
    )
    ids = [InstrumentId.from_str(s) for s in instrument_ids]
    await provider.load_ids_async(ids)
    return [provider.find(i) for i in ids if provider.find(i) is not None]


async def _load_polymarket_for_range(
    series: tuple[str, ...],
    *,
    start_ns: int | None,
    end_ns: int | None,
) -> tuple[list, list[ActivePolymarketMarket]]:
    instruments: list = []
    events: list[ActivePolymarketMarket] = []
    for window_start in _window_starts_sec(start_ns, end_ns):
        for s in series:
            slug = slug_for_series(s, ts=window_start)
            try:
                yes_loader = await PolymarketDataLoader.from_market_slug(slug, token_index=0)
                no_loader = await PolymarketDataLoader.from_market_slug(slug, token_index=1)
            except Exception:  # noqa: BLE001 — expired/invalid slug in range
                continue
            yes_inst = yes_loader.instrument
            no_inst = no_loader.instrument
            instruments.extend([yes_inst, no_inst])
            ts = window_start * 1_000_000_000
            question = str(getattr(yes_inst, "description", None) or "")
            events.append(
                ActivePolymarketMarket(
                    instrument_id=yes_inst.id,
                    no_instrument_id=no_inst.id,
                    series=s,
                    slug=slug,
                    question=question,
                    ts_event=ts,
                    ts_init=ts,
                ),
            )
    return instruments, events


def _align_trade_tick(tick: TradeTick, instrument) -> TradeTick | None:
    price = Price(tick.price.as_double(), instrument.price_precision)
    size = Quantity(tick.size.as_double(), instrument.size_precision)
    if size.raw <= 0:
        return None
    return TradeTick(
        instrument_id=tick.instrument_id,
        price=price,
        size=size,
        aggressor_side=tick.aggressor_side,
        trade_id=tick.trade_id,
        ts_event=tick.ts_event,
        ts_init=tick.ts_init,
    )


def _align_quote_tick(tick: QuoteTick, instrument) -> QuoteTick:
    return QuoteTick(
        instrument_id=tick.instrument_id,
        bid_price=Price(tick.bid_price.as_double(), instrument.price_precision),
        ask_price=Price(tick.ask_price.as_double(), instrument.price_precision),
        bid_size=Quantity(tick.bid_size.as_double(), instrument.size_precision),
        ask_size=Quantity(tick.ask_size.as_double(), instrument.size_precision),
        ts_event=tick.ts_event,
        ts_init=tick.ts_init,
    )


def _normalize_ticks(
    catalog: ParquetDataCatalog,
    instruments: list,
    *,
    start_ns: int | None,
    end_ns: int | None,
) -> tuple[int, int]:
    """Re-write trade/quote ticks aligned to instrument precision (destructive)."""
    by_id = {inst.id: inst for inst in instruments}
    trades_out = 0
    quotes_out = 0

    for iid_str in {str(i) for i in by_id}:
        inst = by_id.get(InstrumentId.from_str(iid_str))
        if inst is None:
            continue
        if str(inst.id).endswith(".BINANCE"):
            rows = catalog.query(data_cls=TradeTick, identifiers=[iid_str])
            if not rows:
                continue
            catalog.delete_data_range(TradeTick, identifier=iid_str)
            aligned = []
            for t in rows:
                aligned_tick = _align_trade_tick(t, inst)
                if aligned_tick is not None:
                    aligned.append(aligned_tick)
            if not aligned:
                continue
            catalog.write_data(aligned, skip_disjoint_check=True)
            trades_out += len(aligned)
        elif str(inst.id).endswith(".POLYMARKET"):
            rows = catalog.query(data_cls=QuoteTick, identifiers=[iid_str])
            if not rows:
                continue
            catalog.delete_data_range(QuoteTick, identifier=iid_str)
            aligned = [_align_quote_tick(t, inst) for t in rows]
            catalog.write_data(aligned, skip_disjoint_check=True)
            quotes_out += len(aligned)

    return trades_out, quotes_out


def prepare_backtest_catalog(
    catalog: ParquetDataCatalog,
    *,
    binance_instruments: tuple[str, ...] = STRATEGY_BINANCE_INSTRUMENTS,
    polymarket_series: tuple[str, ...] | None = None,
    start_ns: int | None = None,
    end_ns: int | None = None,
    normalize_ticks: bool = True,
    load_polymarket: bool = True,
) -> None:
    """
    Write instrument definitions + discovery events; optionally align tick precisions.
    """
    series = polymarket_series or tuple(BINANCE_TO_POLY_SERIES.values())

    binance = asyncio.run(_load_binance_perps(binance_instruments))
    poly_instruments: list = []
    discovery: list[ActivePolymarketMarket] = []
    if load_polymarket and start_ns is not None and end_ns is not None:
        poly_instruments, discovery = asyncio.run(
            _load_polymarket_for_range(series, start_ns=start_ns, end_ns=end_ns),
        )

    to_write = [*binance, *poly_instruments]
    if to_write:
        catalog.write_data(to_write)
    if discovery:
        catalog.write_data(discovery)

    if normalize_ticks and to_write:
        trades_n, quotes_n = _normalize_ticks(
            catalog,
            to_write,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        print(
            f"[backtest-prep] normalized TradeTick rows={trades_n:,} "
            f"QuoteTick rows={quotes_n:,}"
        )

    print(
        f"[backtest-prep] instruments={len(to_write)} "
        f"ActivePolymarketMarket events={len(discovery)}"
    )
