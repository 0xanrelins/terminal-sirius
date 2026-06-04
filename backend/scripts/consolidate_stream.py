#!/usr/bin/env python3
"""
Consolidate StreamingConfig feather (``catalog/live/<instance>/<type>``) into the
queryable ParquetDataCatalog (``catalog/data/<type>``) so backtests can read it.

Native ``ParquetDataCatalog.convert_stream_to_data`` reads the live-run feather files
for each instance + data class and writes them as parquet.

  cd backend && python scripts/consolidate_stream.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.model.data import QuoteTick, TradeTick

from catalog import get_catalog
from recorders.data_types import LiquidationTick

DATA_CLASSES = (TradeTick, QuoteTick, LiquidationTick)


def main() -> None:
    catalog = get_catalog()
    print(f"Catalog: {catalog.path}\n")

    instances = catalog.list_live_runs()
    print(f"Live run instances: {len(instances)}\n")

    for instance_id in instances:
        for data_cls in DATA_CLASSES:
            try:
                catalog.convert_stream_to_data(
                    instance_id, data_cls, subdirectory="live"
                )
            except Exception as e:  # noqa: BLE001 — report and continue
                print(f"  {instance_id[:8]} {data_cls.__name__}: {e!r}")

    print("\nQueryable after consolidation:")
    for data_cls in DATA_CLASSES:
        try:
            rows = catalog.query(data_cls=data_cls)
        except Exception as e:  # noqa: BLE001
            print(f"  {data_cls.__name__}: query failed ({e!r})")
            continue
        if rows:
            ts_min = min(int(r.ts_event) for r in rows)
            ts_max = max(int(r.ts_event) for r in rows)
            print(f"  {data_cls.__name__}: {len(rows):,} rows ({ts_min} … {ts_max})")
        else:
            print(f"  {data_cls.__name__}: 0 rows")


if __name__ == "__main__":
    main()
