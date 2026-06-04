#!/usr/bin/env python3
"""
Run Terminal Sirius backtest from ParquetDataCatalog.

Prerequisites:
  1. Catalog data (streaming / import / LiquidationFeedActor catalog flush)
  2. ``python scripts/run_terminal_sirius_backtest.py --prepare`` (instruments + discovery)
  3. If precision errors: ``--prepare --prepare-normalize`` (slow; re-writes trade/quote parquet)

Usage:
  cd backend && python scripts/run_terminal_sirius_backtest.py --prepare
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

from nautilus_trader.adapters.binance import BinanceFuturesLiquidation
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.model.data import QuoteTick, TradeTick

from backtest.catalog_prep import prepare_backtest_catalog
from backtest.register_custom_data import register_terminal_sirius_custom_data
from backtest.run_config import _iso_to_ns, build_terminal_sirius_run_config
from catalog import get_catalog
from recorders.data_types import LiquidationTick


def _catalog_has_minimum_rows(catalog, *, min_trades: int = 100) -> bool:
    from strategies.mapping import STRATEGY_BINANCE_INSTRUMENTS

    trade_total = 0
    for sym in STRATEGY_BINANCE_INSTRUMENTS:
        rows = catalog.query(data_cls=TradeTick, identifiers=[sym])
        trade_total += len(rows)
        print(f"  TradeTick {sym}: {len(rows):,}")
    liq_ticks = catalog.query(data_cls=LiquidationTick)
    liq_native = catalog.query(data_cls=BinanceFuturesLiquidation)
    total_liq = len(liq_ticks) + len(liq_native)
    print(f"TradeTick rows (Binance strategy symbols): {trade_total:,}")
    print(
        f"Liquidation rows: {total_liq:,} "
        f"(ticks={len(liq_ticks):,}, native={len(liq_native):,})"
    )
    quotes = catalog.query(data_cls=QuoteTick)
    print(f"QuoteTick rows: {len(quotes):,}")
    if trade_total < min_trades:
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
        "--prepare",
        action="store_true",
        help="Fetch Binance/Polymarket instruments + write ActivePolymarketMarket events",
    )
    p.add_argument(
        "--prepare-normalize",
        action="store_true",
        help="With --prepare, re-write all Trade/Quote ticks to exchange precision (slow)",
    )
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

    start_ns = _iso_to_ns(args.start)
    end_ns = _iso_to_ns(args.end)

    register_terminal_sirius_custom_data()

    if args.prepare:
        print("Preparing catalog for backtest…")
        prepare_backtest_catalog(
            catalog,
            start_ns=start_ns,
            end_ns=end_ns,
            normalize_ticks=args.prepare_normalize,
            load_polymarket=not args.actors_only,
        )
        if not args.prepare_normalize:
            print(
                "(tick normalization skipped — use --prepare-normalize if backtest "
                "fails on price/size precision)"
            )
        print()

    run_config = build_terminal_sirius_run_config(
        catalog_path=catalog.path,
        start_time=args.start,
        end_time=args.end,
        include_strategy=not args.actors_only,
        include_polymarket_quotes=not args.actors_only,
        log_level=args.log_level,
    )

    node = BacktestNode(configs=[run_config])
    print("Running backtest…")
    try:
        results = node.run()
    except Exception as e:
        print(f"\nBacktest failed: {e}", file=sys.stderr)
        print(
            "If this is the first run, try:\n"
            "  python scripts/run_terminal_sirius_backtest.py --prepare "
            "[--start … --end …]",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        node.dispose()

    if results:
        print(f"Done. Runs: {len(results)}")
    else:
        print("Backtest finished (no result objects returned).")


if __name__ == "__main__":
    main()
