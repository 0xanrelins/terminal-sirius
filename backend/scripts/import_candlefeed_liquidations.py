#!/usr/bin/env python3
"""
Replace liquidation_bars for major coins from CandleFeed tick JSON exports.

Reads *_liquidations_*.json (time, side, usd_value) and aggregates into the same
long/short buckets as the live Binance stream (sell → long, buy → short).

Usage (from repo root):
  cd backend && python scripts/import_candlefeed_liquidations.py
  cd backend && python scripts/import_candlefeed_liquidations.py --dry-run
  cd backend && python scripts/import_candlefeed_liquidations.py --data-dir /path/to/raw
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_BACKEND = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))

load_dotenv()
load_dotenv(_REPO_ROOT / ".env")

import db  # noqa: E402
from liquidations import (  # noqa: E402
    INTERVAL_SECONDS,
    binance_to_nautilus,
    bucket_time,
)

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT")
DEFAULT_DATA_DIR = Path("/Users/0xanrelins/Documents/candlefeed/data/raw")
INSERT_BATCH = 5000


def parse_tick_time(iso: str) -> int:
    """ISO8601 tick time → unix ms."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def candlefeed_side_to_binance(side: str) -> str:
    """CandleFeed sell/buy → Binance SELL/BUY (same as forceOrder semantics)."""
    s = side.strip().lower()
    if s == "sell":
        return "SELL"
    if s == "buy":
        return "BUY"
    raise ValueError(f"unknown side: {side!r}")


def find_liquidation_files(data_dir: Path, symbols: tuple[str, ...]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for sym in symbols:
        matches = sorted(data_dir.glob(f"{sym}_liquidations_*.json"))
        if not matches:
            raise FileNotFoundError(f"No liquidation file for {sym} in {data_dir}")
        out[sym] = matches[-1]
    return out


def aggregate_file(path: Path) -> dict[tuple[str, int], dict[str, float]]:
    """symbol-level buckets keyed by (interval, time)."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    rows = doc.get("data") or []
    buckets: dict[tuple[str, int], dict[str, float]] = defaultdict(
        lambda: {"long": 0.0, "short": 0.0}
    )
    skipped = 0
    for row in rows:
        try:
            trade_ms = parse_tick_time(str(row["time"]))
            side = candlefeed_side_to_binance(str(row["side"]))
            notional = float(row["usd_value"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        long_delta = notional if side == "SELL" else 0.0
        short_delta = notional if side == "BUY" else 0.0
        for interval in INTERVAL_SECONDS:
            t = bucket_time(trade_ms, interval)
            key = (interval, t)
            buckets[key]["long"] += long_delta
            buckets[key]["short"] += short_delta
    if skipped:
        print(f"  {path.name}: skipped {skipped} malformed rows")
    print(f"  {path.name}: {len(rows)} ticks → {len(buckets)} bar keys")
    return buckets


async def replace_bars(
    nautilus_symbols: list[str],
    all_rows: list[tuple[str, str, int, float, float]],
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        print(f"[dry-run] would DELETE bars for {len(nautilus_symbols)} symbols")
        print(f"[dry-run] would INSERT {len(all_rows)} liquidation_bars rows")
        return

    await db.init()
    try:
        deleted = await db.pool().execute(
            "DELETE FROM liquidation_bars WHERE symbol = ANY($1::text[])",
            nautilus_symbols,
        )
        print(f"Deleted existing rows: {deleted}")

        for i in range(0, len(all_rows), INSERT_BATCH):
            batch = all_rows[i : i + INSERT_BATCH]
            await db.pool().executemany(
                """
                INSERT INTO liquidation_bars (symbol, interval, time, long, short)
                VALUES ($1, $2, $3, $4, $5)
                """,
                batch,
            )
            if (i + INSERT_BATCH) % 50_000 == 0 or i + INSERT_BATCH >= len(all_rows):
                print(f"  inserted {min(i + INSERT_BATCH, len(all_rows))}/{len(all_rows)}")
    finally:
        await db.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import CandleFeed liquidation ticks into DB")
    p.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory with *_liquidations_*.json (default: {DEFAULT_DATA_DIR})",
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help="Binance symbols to import",
    )
    p.add_argument("--dry-run", action="store_true", help="Aggregate only, no DB writes")
    return p.parse_args()


async def main_async() -> None:
    args = parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        print(f"Data dir not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    symbols = tuple(s.upper() for s in args.symbols)
    files = find_liquidation_files(data_dir, symbols)
    nautilus_symbols = [binance_to_nautilus(s) for s in symbols]

    merged: dict[tuple[str, str, int], dict[str, float]] = defaultdict(
        lambda: {"long": 0.0, "short": 0.0}
    )

    print(f"Import from {data_dir}")
    for bin_sym, path in files.items():
        naut = binance_to_nautilus(bin_sym)
        per_sym = aggregate_file(path)
        for (interval, t), vals in per_sym.items():
            key = (naut, interval, t)
            merged[key]["long"] += vals["long"]
            merged[key]["short"] += vals["short"]

    rows: list[tuple[str, str, int, float, float]] = []
    for (symbol, interval, t), vals in merged.items():
        long_v = round(vals["long"], 2)
        short_v = round(vals["short"], 2)
        if long_v == 0.0 and short_v == 0.0:
            continue
        rows.append((symbol, interval, t, long_v, short_v))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    print(f"Total bar rows to write: {len(rows)}")
    if rows:
        t_min = min(r[2] for r in rows)
        t_max = max(r[2] for r in rows)
        print(f"Time range (unix sec): {t_min} → {t_max}")

    await replace_bars(nautilus_symbols, rows, dry_run=args.dry_run)
    print("Done.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
