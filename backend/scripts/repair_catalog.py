#!/usr/bin/env python3
"""
Repair a broken ParquetDataCatalog (mixed tick precision metadata, partial writes).

Steps:
  1. Remove corrupted ``data/trade_tick`` parquet for Binance perps (keeps Polymarket ticks).
  2. Remove stale duplicate ``data/crypto_perpetual`` instrument files from bad prep runs.
  3. ``convert_stream_to_data`` — rebuild Binance TradeTicks from ``catalog/live/`` feathers.
  4. ``prepare_backtest_catalog`` — exchange instruments + optional tick precision align.

Usage:
  cd backend && python scripts/repair_catalog.py
  cd backend && python scripts/repair_catalog.py --skip-normalize
  cd backend && python scripts/repair_catalog.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.model.data import QuoteTick, TradeTick

from backtest.catalog_prep import _align_trade_tick, prepare_backtest_catalog
from backtest.register_custom_data import register_terminal_sirius_custom_data
from catalog import get_catalog
from recorders.data_types import LiquidationTick
from strategies.mapping import STRATEGY_BINANCE_INSTRUMENTS

BINANCE_TRADE_DIRS = STRATEGY_BINANCE_INSTRUMENTS + ("HYPEUSDT-PERP.BINANCE",)
CONSOLIDATE_CLASSES = (TradeTick, QuoteTick, LiquidationTick)


def _archive_polymarket_trade_ticks(catalog_path: Path, *, dry_run: bool) -> int:
    """
    Move Polymarket trade_tick dirs out of the catalog tree.

    Rust ``catalog.query(TradeTick)`` registers all ``trade_tick/*`` leaves; mixed
    Polymarket metadata breaks Binance queries. Backtest uses ``QuoteTick`` for PM.
    """
    base = catalog_path / "data" / "trade_tick"
    archive = catalog_path / "data" / "_trade_tick_polymarket_archived"
    moved = 0
    if not base.is_dir():
        return 0
    if not dry_run:
        archive.mkdir(parents=True, exist_ok=True)
    for entry in base.iterdir():
        if not entry.is_dir() or not entry.name.endswith(".POLYMARKET"):
            continue
        moved += 1
        if dry_run:
            print(f"[dry-run] would archive {entry.name}")
        else:
            dest = archive / entry.name
            if dest.exists():
                import shutil

                shutil.rmtree(dest)
            entry.rename(dest)
    return moved


def _purge_binance_trade_parquet(
    catalog_path: Path,
    *,
    dry_run: bool,
    symbols: tuple[str, ...] = BINANCE_TRADE_DIRS,
) -> int:
    base = catalog_path / "data" / "trade_tick"
    removed = 0
    for iid in symbols:
        directory = base / iid
        if not directory.is_dir():
            continue
        for parquet in directory.glob("*.parquet"):
            removed += 1
            if dry_run:
                print(f"[dry-run] would delete {parquet}")
            else:
                parquet.unlink()
    return removed


def _purge_instrument_parquet(catalog_path: Path, *, dry_run: bool) -> int:
    """Remove instrument defs so prepare can rewrite from Binance/Polymarket loaders."""
    removed = 0
    for sub in ("crypto_perpetual", "binary_option"):
        base = catalog_path / "data" / sub
        if not base.is_dir():
            continue
        for parquet in base.rglob("*.parquet"):
            removed += 1
            if dry_run:
                print(f"[dry-run] would delete instrument {parquet}")
            else:
                parquet.unlink()
    return removed


async def _load_binance_instruments() -> dict[str, object]:
    from backtest.catalog_prep import _load_binance_perps

    instruments = await _load_binance_perps(BINANCE_TRADE_DIRS)
    return {str(inst.id): inst for inst in instruments}


def _rebuild_binance_trades_from_live(
    catalog,
    instruments: dict[str, object],
    *,
    dry_run: bool,
    symbols: tuple[str, ...] = BINANCE_TRADE_DIRS,
) -> dict[str, int]:
    """
    Rebuild Binance ``TradeTick`` parquet from live feathers (one write per symbol).

    Avoids ``convert_stream_to_data`` appending files with conflicting Arrow metadata.
    """
    by_symbol: dict[str, list] = {sym: [] for sym in symbols}
    instances = catalog.list_live_runs()
    print(f"Reading TradeTick feathers from {len(instances)} live instance(s) …")

    for instance_id in instances:
        try:
            batch = catalog._read_feather(  # noqa: SLF001 — native catalog API
                kind="live",
                instance_id=instance_id,
                data_cls=TradeTick,
                identifiers=list(symbols),
            )
        except Exception as e:  # noqa: BLE001
            print(f"  {instance_id[:8]}… TradeTick: {e!r}")
            continue
        for tick in batch:
            sym = str(tick.instrument_id)
            if sym in by_symbol:
                by_symbol[sym].append(tick)

    counts: dict[str, int] = {}
    for sym, ticks in by_symbol.items():
        counts[sym] = len(ticks)
        if dry_run or not ticks:
            continue
        inst = instruments.get(sym)
        if inst is None:
            print(f"  {sym}: no instrument definition, skipped")
            continue
        aligned: list = []
        for tick in ticks:
            aligned_tick = _align_trade_tick(tick, inst)
            if aligned_tick is not None:
                aligned.append(aligned_tick)
        if not aligned:
            continue
        aligned.sort(key=lambda t: t.ts_init)
        trade_dir = Path(catalog.path) / "data" / "trade_tick" / sym
        if trade_dir.is_dir():
            for parquet in trade_dir.glob("*.parquet"):
                parquet.unlink()
        catalog.write_data(aligned, skip_disjoint_check=True)
        print(f"  wrote {sym}: {len(aligned):,} TradeTicks (from {len(ticks):,} feathers)")
    return counts


def _consolidate_live_non_trade(catalog, *, dry_run: bool) -> None:
    """Consolidate QuoteTick + LiquidationTick feathers (quotes already queryable)."""
    instances = catalog.list_live_runs()
    if dry_run:
        return
    register_terminal_sirius_custom_data()
    for instance_id in instances:
        for data_cls in (QuoteTick, LiquidationTick):
            try:
                catalog.convert_stream_to_data(
                    instance_id,
                    data_cls,
                    subdirectory="live",
                )
            except Exception as e:  # noqa: BLE001
                print(f"  {instance_id[:8]}… {data_cls.__name__}: {e!r}")


def _print_stats(catalog) -> None:
    print("\nCatalog inventory:")
    for data_cls in (*CONSOLIDATE_CLASSES,):
        if data_cls is TradeTick:
            total = 0
            ts_min = None
            ts_max = None
            for sym in BINANCE_TRADE_DIRS:
                try:
                    rows = catalog.query(data_cls=TradeTick, identifiers=[sym])
                except Exception as e:  # noqa: BLE001
                    print(f"  TradeTick[{sym}]: query failed ({e!r})")
                    total = -1
                    break
                total += len(rows)
                if rows:
                    lo = min(int(r.ts_event) for r in rows)
                    hi = max(int(r.ts_event) for r in rows)
                    ts_min = lo if ts_min is None else min(ts_min, lo)
                    ts_max = hi if ts_max is None else max(ts_max, hi)
            if total >= 0:
                print(f"  TradeTick (Binance): {total:,} rows ({ts_min} … {ts_max})")
            continue
        try:
            rows = catalog.query(data_cls=data_cls)
        except Exception as e:  # noqa: BLE001
            print(f"  {data_cls.__name__}: query failed ({e!r})")
            continue
        if not rows:
            print(f"  {data_cls.__name__}: 0 rows")
            continue
        ts_min = min(int(r.ts_event) for r in rows)
        ts_max = max(int(r.ts_event) for r in rows)
        print(f"  {data_cls.__name__}: {len(rows):,} rows ({ts_min} … {ts_max})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repair ParquetDataCatalog for backtest")
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--skip-normalize",
        action="store_true",
        help="Skip tick precision re-write after consolidate",
    )
    p.add_argument(
        "--skip-prepare",
        action="store_true",
        help="Only purge + consolidate (no instruments/discovery)",
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        metavar="INSTRUMENT_ID",
        help="Rebuild only these Binance perps (e.g. BTCUSDT-PERP.BINANCE); "
        "does not purge other symbols' parquet",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    catalog = get_catalog(args.catalog)
    path = Path(catalog.path)
    symbols = tuple(args.symbols) if args.symbols else BINANCE_TRADE_DIRS
    partial = args.symbols is not None
    print(f"Catalog: {path}\n")
    if partial:
        print(f"Partial repair symbols: {', '.join(symbols)}\n")

    n_pm = 0 if partial else _archive_polymarket_trade_ticks(path, dry_run=args.dry_run)
    n_trade = _purge_binance_trade_parquet(path, dry_run=args.dry_run, symbols=symbols)
    n_inst = 0 if partial else _purge_instrument_parquet(path, dry_run=args.dry_run)
    print(f"Archived {n_pm} Polymarket trade_tick director(ies) → data/_trade_tick_polymarket_archived/")
    print(f"Purged {n_trade} Binance trade parquet file(s)")
    print(f"Purged {n_inst} instrument parquet file(s)\n")

    if args.dry_run:
        print("Dry run — stopping before consolidate/prepare.")
        return

    import asyncio

    print("Loading Binance instrument definitions …")
    instruments = asyncio.run(_load_binance_instruments())
    print("Rebuilding Binance TradeTicks from live feathers (precision-aligned) …")
    trade_counts = _rebuild_binance_trades_from_live(
        catalog, instruments, dry_run=False, symbols=symbols,
    )
    rebuilt = sum(1 for n in trade_counts.values() if n > 0)
    print(f"Binance trade symbols rebuilt: {rebuilt}")
    if trade_counts.get("BTCUSDT-PERP.BINANCE", 0) == 0:
        print(
            "[warn] BTCUSDT-PERP.BINANCE: no live feathers left — "
            "re-stream with CATALOG_STREAMING_ENABLED or restore archive."
        )

    if not partial:
        print("Consolidating QuoteTick / LiquidationTick feathers (skip if exists) …")
        _consolidate_live_non_trade(catalog, dry_run=False)

    if not args.skip_prepare and not partial:
        register_terminal_sirius_custom_data()
        start_ns = None
        end_ns = None
        start_ns = None
        end_ns = None
        try:
            ts_lo = catalog.query_first_timestamp(TradeTick, identifier="BTCUSDT-PERP.BINANCE")
            ts_hi = catalog.query_last_timestamp(TradeTick, identifier="BTCUSDT-PERP.BINANCE")
            if ts_lo is not None:
                start_ns = int(ts_lo.value)
            if ts_hi is not None:
                end_ns = int(ts_hi.value)
        except Exception:  # noqa: BLE001
            pass
        print("\nPreparing Binance instruments …")
        prepare_backtest_catalog(
            catalog,
            start_ns=start_ns,
            end_ns=end_ns,
            normalize_ticks=False,
            load_polymarket=False,
        )

    _print_stats(catalog)
    print("\nRepair done.")


if __name__ == "__main__":
    main()
