#!/usr/bin/env python3
"""Print row counts and time ranges in the ParquetDataCatalog."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.adapters.binance import BinanceFuturesLiquidation
from nautilus_trader.model.data import QuoteTick, TradeTick

from catalog import get_catalog
from recorders.data_types import BinanceSecondPrice, LiquidationTick, PolymarketSecondPrice


def _count_and_range(catalog, data_cls, **query_kw) -> None:
    name = getattr(data_cls, "__name__", str(data_cls))
    try:
        rows = catalog.query(data_cls=data_cls, **query_kw)
    except Exception as e:
        print(f"  {name}: query failed ({e!r})")
        return
    if not rows:
        print(f"  {name}: 0 rows")
        return
    ts_min = min(int(r.ts_event) for r in rows)
    ts_max = max(int(r.ts_event) for r in rows)
    print(f"  {name}: {len(rows):,} rows  ({ts_min} … {ts_max})")


def main() -> None:
    p = argparse.ArgumentParser(description="Catalog inventory")
    p.add_argument("--catalog", type=Path, default=None)
    args = p.parse_args()

    catalog = get_catalog(args.catalog)
    print(f"Catalog: {catalog.path}\n")

    for cls in (
        TradeTick,
        QuoteTick,
        LiquidationTick,
        BinanceFuturesLiquidation,
        BinanceSecondPrice,
        PolymarketSecondPrice,
    ):
        _count_and_range(catalog, cls)


if __name__ == "__main__":
    main()
