#!/usr/bin/env python3
"""
Write Binance USDT-M perp (and optional Polymarket) instrument definitions to the catalog.

Backtest requires instruments in the catalog before ``BacktestDataConfig`` replay.

Usage:
  cd backend && python scripts/write_instruments_to_catalog.py
  cd backend && python scripts/write_instruments_to_catalog.py --polymarket-slug btc-updown-15m-1778931900
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity

from catalog import get_catalog
from liquidations import binance_to_nautilus
from strategies.mapping import STRATEGY_BINANCE_INSTRUMENTS


def _binance_perp(symbol: str) -> CryptoPerpetual:
    """Minimal USDT-M perp definition for catalog/backtest (public specs)."""
    base = symbol.replace("USDT", "")
    base_ccy = Currency.from_str(base)
    quote = Currency.from_str("USDT")
    iid = InstrumentId(Symbol(f"{symbol}-PERP"), BINANCE_VENUE)
    return CryptoPerpetual(
        instrument_id=iid,
        raw_symbol=Symbol(symbol),
        base_currency=base_ccy,
        quote_currency=quote,
        settlement_currency=quote,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(5, quote),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str("0.01"),
        margin_init=__import__("decimal").Decimal("0.05"),
        margin_maint=__import__("decimal").Decimal("0.025"),
        maker_fee=__import__("decimal").Decimal("0.0002"),
        taker_fee=__import__("decimal").Decimal("0.0004"),
        ts_event=0,
        ts_init=0,
    )


async def _polymarket_from_slug(slug: str):
    from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader

    loader = await PolymarketDataLoader.from_market_slug(slug, token_index=0)
    return loader.instrument


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Write instrument definitions to catalog")
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument(
        "--symbols",
        nargs="+",
        default=[s.split("-")[0] for s in STRATEGY_BINANCE_INSTRUMENTS],
        help="Binance symbols e.g. BTCUSDT",
    )
    p.add_argument("--polymarket-slug", action="append", default=[], dest="slugs")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    catalog = get_catalog(args.catalog)
    instruments = [_binance_perp(sym.upper()) for sym in args.symbols]

    for slug in args.slugs:
        instruments.append(asyncio.run(_polymarket_from_slug(slug)))

    catalog.write_data(instruments)
    print(f"Wrote {len(instruments)} instruments → {catalog.path}")
    for inst in instruments:
        print(f"  {inst.id}")


if __name__ == "__main__":
    main()
