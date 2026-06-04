#!/usr/bin/env python3
"""
Run Terminal Sirius backtest from ParquetDataCatalog.

Prerequisites:
  1. ``python scripts/write_instruments_to_catalog.py``
  2. Catalog data: live ``StreamingConfig`` (``CATALOG_STREAMING_ENABLED=1``) and/or
     ``python scripts/import_to_catalog.py`` / ``sync_to_catalog.py``
  3. ``python scripts/catalog_stats.py`` — verify TradeTick + liquidation rows

Usage:
  cd backend && python scripts/run_terminal_sirius_backtest.py
  cd backend && python scripts/run_terminal_sirius_backtest.py --start 2026-06-01 --end 2026-06-03
  cd backend && python scripts/run_terminal_sirius_backtest.py --actors-only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.model.data import QuoteTick, TradeTick

from backtest.run_config import build_terminal_sirius_run_config
from catalog import get_catalog
from nautilus_trader.adapters.binance import BinanceFuturesLiquidation

from recorders.data_types import LiquidationTick


def _catalog_has_minimum_rows(catalog, *, min_trades: int = 100) -> bool:
    trades = catalog.query(data_cls=TradeTick)
    liq_ticks = catalog.query(data_cls=LiquidationTick)
    liq_native = catalog.query(data_cls=BinanceFuturesLiquidation)
    total_liq = len(liq_ticks) + len(liq_native)
    print(f"TradeTick rows: {len(trades):,}")
    print(
        f"Liquidation rows: {total_liq:,} "
        f"(ticks={len(liq_ticks):,}, native={len(liq_native):,})"
    )
    quotes = catalog.query(data_cls=QuoteTick)
    print(f"QuoteTick rows: {len(quotes):,}")
    if len(trades) < min_trades:
        print(
            f"\nNeed at least {min_trades} TradeTicks for a meaningful run. "
            "Enable CATALOG_STREAMING_ENABLED=1 on the live node for ~900s+ or import CandleFeed data."
        )
        return False
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Terminal Sirius catalog backtest")
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument("--start", type=str, default=None, help="ISO UTC start")
    p.add_argument("--end", type=str, default=None, help="ISO UTC end")
    p.add_argument(
        "--actors-only",
        action="store_true",
        help="Run signal actors without TerminalSiriusStrategy (data/indicator smoke)",
    )
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    catalog = get_catalog(args.catalog)
    print(f"Catalog: {catalog.path}\n")

    if not _catalog_has_minimum_rows(catalog, min_trades=args.min_trades):
        sys.exit(1)

    run_config = build_terminal_sirius_run_config(
        catalog_path=catalog.path,
        start_time=args.start,
        end_time=args.end,
        include_strategy=not args.actors_only,
        log_level=args.log_level,
    )

    node = BacktestNode(configs=[run_config])
    print("\nRunning backtest…")
    results = node.run()
    if results:
        print(f"Done. Runs: {len(results)}")
    node.dispose()


if __name__ == "__main__":
    main()
