#!/usr/bin/env python3
"""
Rebuild LiquidationTick parquet data from live stream feathers.

Purpose:
  - Recover from non-disjoint interval errors in data/custom_liquidation_tick.
  - Recreate queryable liquidation parquet with a single native write pass.

Usage:
  cd backend && python scripts/repair_liquidation_catalog.py
  cd backend && python scripts/repair_liquidation_catalog.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from catalog import get_catalog
from recorders.data_types import LiquidationTick

_STATE_FILE = ".liq_stream_state.json"


def _load_from_live(catalog) -> list[LiquidationTick]:
    ticks: list[LiquidationTick] = []
    instances = catalog.list_live_runs()
    print(f"Live run instances: {len(instances)}")
    for instance_id in instances:
        try:
            batch = catalog._read_feather(  # noqa: SLF001 - native catalog helper used by repair scripts
                kind="live",
                instance_id=instance_id,
                data_cls=LiquidationTick,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  {instance_id[:8]}… LiquidationTick read failed: {e!r}")
            continue
        print(f"  {instance_id[:8]}… LiquidationTick: {len(batch)}")
        ticks.extend(batch)
    return ticks


def _dedupe_and_sort(ticks: list[LiquidationTick]) -> list[LiquidationTick]:
    ticks.sort(key=lambda t: t.ts_init)
    out: list[LiquidationTick] = []
    seen: set[tuple] = set()
    for t in ticks:
        key = (
            int(t.ts_init),
            str(t.symbol),
            str(t.side),
            float(t.price),
            float(t.quantity),
            float(t.notional),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _archive_existing(target_dir: Path, *, dry_run: bool) -> Path | None:
    if not target_dir.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = target_dir.parent / f"{target_dir.name}_archived_{ts}"
    if dry_run:
        print(f"[dry-run] would move {target_dir} -> {archived}")
        return archived
    target_dir.rename(archived)
    print(f"Archived existing directory -> {archived}")
    return archived


def _save_watermark(catalog_path: Path, ts_init: int, *, dry_run: bool) -> None:
    payload = {"last_written_ts_init": int(ts_init)}
    state_file = catalog_path / _STATE_FILE
    if dry_run:
        print(f"[dry-run] would write watermark {payload} -> {state_file}")
        return
    state_file.write_text(json.dumps(payload), encoding="utf-8")
    print(f"Wrote watermark -> {state_file}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repair custom_liquidation_tick parquet data")
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    catalog = get_catalog(args.catalog)
    catalog_path = Path(catalog.path)
    target_dir = catalog_path / "data" / "custom_liquidation_tick"

    print(f"Catalog: {catalog_path}")
    ticks = _load_from_live(catalog)
    if not ticks:
        print("No LiquidationTick data found in live feathers; abort.")
        sys.exit(1)

    cleaned = _dedupe_and_sort(ticks)
    print(f"Collected ticks: {len(ticks):,}")
    print(f"After dedupe: {len(cleaned):,}")
    print(f"Range ts_init: {int(cleaned[0].ts_init)} -> {int(cleaned[-1].ts_init)}")

    _archive_existing(target_dir, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[dry-run] would write {len(cleaned):,} LiquidationTick rows")
        _save_watermark(catalog_path, int(cleaned[-1].ts_init), dry_run=True)
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    catalog.write_data(cleaned, skip_disjoint_check=True)
    print(f"Wrote {len(cleaned):,} LiquidationTick rows -> {target_dir}")
    _save_watermark(catalog_path, int(cleaned[-1].ts_init), dry_run=False)
    print("Repair done.")


if __name__ == "__main__":
    main()
