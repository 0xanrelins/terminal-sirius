#!/usr/bin/env python3
"""
Import raw CandleFeed JSON exports into the Nautilus ParquetDataCatalog.

Reads from --data-dir (default: /Users/0xanrelins/Documents/candlefeed/data/raw):
  *_liquidations_*.json  →  LiquidationTick custom data objects
  *_candles_1m_*.json    →  Bar (1-MINUTE-LAST-EXTERNAL) objects

Usage:
  cd backend && python scripts/import_to_catalog.py
  cd backend && python scripts/import_to_catalog.py --data-dir /path/to/raw
  cd backend && python scripts/import_to_catalog.py --catalog /path/to/catalog
  cd backend && python scripts/import_to_catalog.py --dry-run
  cd backend && python scripts/import_to_catalog.py --symbols BTCUSDT ETHUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity

from catalog import get_catalog
from liquidations import binance_to_nautilus
from recorders.data_types import LiquidationTick

DEFAULT_DATA_DIR = Path("/Users/0xanrelins/Documents/candlefeed/data/raw")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT")


# ── helpers ──────────────────────────────────────────────────────────────────

def _iso_to_ns(iso: str) -> int:
    """ISO8601 string → nanoseconds (int)."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _ms_to_ns(ms: int) -> int:
    return ms * 1_000_000


def _candlefeed_side(raw: str) -> str:
    """CandleFeed 'buy'/'sell' → Binance convention SELL/BUY."""
    s = raw.strip().lower()
    if s == "sell":
        return "SELL"
    if s == "buy":
        return "BUY"
    raise ValueError(f"unknown side: {raw!r}")


def _price_str(v: float) -> str:
    """Format price to 1 decimal for USDT-perp markets (avoids over-precision)."""
    return f"{v:.1f}"


def _qty_str(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".")


# ── liquidation ticks ─────────────────────────────────────────────────────────

def load_liquidation_ticks(
    data_dir: Path,
    symbols: tuple[str, ...],
) -> list[LiquidationTick]:
    ticks: list[LiquidationTick] = []
    for sym in symbols:
        files = sorted(data_dir.glob(f"{sym}_liquidations_*.json"))
        if not files:
            print(f"  [warn] no liquidation file for {sym}", file=sys.stderr)
            continue
        path = files[-1]
        naut_sym = binance_to_nautilus(sym)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        rows = doc.get("data") or []
        skipped = 0
        for row in rows:
            try:
                ts_ns = _iso_to_ns(str(row["time"]))
                side = _candlefeed_side(str(row["side"]))
                price = float(row["price"])
                quantity = float(row["quantity"])
                usd_value = float(row["usd_value"])
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            ticks.append(
                LiquidationTick(
                    symbol=naut_sym,
                    side=side,
                    price=price,
                    quantity=quantity,
                    usd_value=usd_value,
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
            )
        if skipped:
            print(f"  {path.name}: skipped {skipped} malformed rows")
        print(f"  {path.name}: {len(rows) - skipped} liquidation ticks loaded")
    ticks.sort(key=lambda t: t.ts_init)
    return ticks


# ── bars ──────────────────────────────────────────────────────────────────────

def load_bars(
    data_dir: Path,
    symbols: tuple[str, ...],
) -> list[Bar]:
    bars: list[Bar] = []
    for sym in symbols:
        files = sorted(data_dir.glob(f"{sym}_candles_1m_*.json"))
        if not files:
            print(f"  [warn] no candles file for {sym}", file=sys.stderr)
            continue
        path = files[-1]
        naut_sym = binance_to_nautilus(sym)
        bar_type = BarType.from_str(f"{naut_sym}-1-MINUTE-LAST-EXTERNAL")
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        rows = doc.get("data") or []
        skipped = 0
        for row in rows:
            try:
                bar_open_ns = _iso_to_ns(str(row["time"]))
                ts_event = bar_open_ns + 60_000_000_000  # close = open + 60s
                bars.append(
                    Bar(
                        bar_type=bar_type,
                        open=Price.from_str(_price_str(float(row["open"]))),
                        high=Price.from_str(_price_str(float(row["high"]))),
                        low=Price.from_str(_price_str(float(row["low"]))),
                        close=Price.from_str(_price_str(float(row["close"]))),
                        volume=Quantity.from_str(_qty_str(float(row["volume"]))),
                        ts_event=ts_event,
                        ts_init=ts_event,
                    )
                )
            except (KeyError, TypeError, ValueError) as e:
                skipped += 1
                continue
        if skipped:
            print(f"  {path.name}: skipped {skipped} malformed candle rows")
        print(f"  {path.name}: {len(rows) - skipped} bars loaded")
    bars.sort(key=lambda b: b.ts_init)
    return bars


# ── write ─────────────────────────────────────────────────────────────────────

def write_to_catalog(
    ticks: list[LiquidationTick],
    bars: list[Bar],
    catalog_path: Path | None,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"[dry-run] would write {len(ticks)} LiquidationTick objects")
        print(f"[dry-run] would write {len(bars)} Bar objects")
        return

    catalog = get_catalog(catalog_path)
    if ticks:
        catalog.write_data(ticks)
        print(f"Wrote {len(ticks)} LiquidationTick objects → {catalog.path}")
    if bars:
        catalog.write_data(bars)
        print(f"Wrote {len(bars)} Bar objects → {catalog.path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import CandleFeed raw JSON into DataCatalog")
    p.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR,
        help=f"Directory with raw JSON files (default: {DEFAULT_DATA_DIR})",
    )
    p.add_argument(
        "--catalog", type=Path, default=None,
        help="Catalog directory (default: backend/catalog/data or $CATALOG_PATH)",
    )
    p.add_argument(
        "--symbols", nargs="+", default=list(DEFAULT_SYMBOLS),
        help="Binance symbols to import",
    )
    p.add_argument("--dry-run", action="store_true", help="Parse only, no writes")
    p.add_argument("--no-ticks", action="store_true", help="Skip liquidation ticks")
    p.add_argument("--no-bars", action="store_true", help="Skip candle bars")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        print(f"Data dir not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    symbols = tuple(s.upper() for s in args.symbols)
    print(f"Import from {data_dir}")
    print(f"Symbols: {', '.join(symbols)}")

    ticks: list[LiquidationTick] = []
    bars: list[Bar] = []

    if not args.no_ticks:
        print("\n--- Liquidation ticks ---")
        ticks = load_liquidation_ticks(data_dir, symbols)

    if not args.no_bars:
        print("\n--- Candle bars ---")
        bars = load_bars(data_dir, symbols)

    print(f"\nTotal: {len(ticks)} ticks, {len(bars)} bars")
    write_to_catalog(ticks, bars, args.catalog, dry_run=args.dry_run)
    print("Done.")


if __name__ == "__main__":
    main()
