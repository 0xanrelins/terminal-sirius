#!/usr/bin/env python3
"""
Incremental sync: append only new data from CandleFeed exports to the DataCatalog.

Reads a watermark file (catalog/data/.sync_state.json) to know the last written
timestamp per symbol+type, then appends only rows newer than that watermark.

Designed for cron:
  0 4 * * *  cd /path/to/backend && python scripts/sync_to_catalog.py >> /var/log/sirius_sync.log 2>&1

Usage:
  cd backend && python scripts/sync_to_catalog.py
  cd backend && python scripts/sync_to_catalog.py --hours 48  # re-sync last 48 h
  cd backend && python scripts/sync_to_catalog.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity

from catalog import get_catalog
from liquidations import binance_to_nautilus
from strategies.liq_poly_data import LiquidationTick

DEFAULT_DATA_DIR = Path("/Users/0xanrelins/Documents/candlefeed/data/raw")
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT")
SYNC_STATE_FILE = ".sync_state.json"


# ── watermark helpers ─────────────────────────────────────────────────────────

def _load_state(catalog_path: Path) -> dict[str, Any]:
    fp = catalog_path / SYNC_STATE_FILE
    if fp.exists():
        try:
            return json.loads(fp.read_text())
        except Exception:
            pass
    return {}


def _save_state(catalog_path: Path, state: dict[str, Any]) -> None:
    fp = catalog_path / SYNC_STATE_FILE
    fp.write_text(json.dumps(state, indent=2))


def _watermark_key(sym: str, kind: str) -> str:
    return f"{sym}:{kind}"


# ── shared from import_to_catalog ────────────────────────────────────────────

def _iso_to_ns(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _candlefeed_side(raw: str) -> str:
    s = raw.strip().lower()
    if s == "sell":
        return "SELL"
    if s == "buy":
        return "BUY"
    raise ValueError(f"unknown side: {raw!r}")


def _price_str(v: float) -> str:
    return f"{v:.1f}"


def _qty_str(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".")


# ── incremental loaders ───────────────────────────────────────────────────────

def sync_ticks(
    data_dir: Path,
    sym: str,
    after_ns: int,
    cutoff_ns: int,
) -> tuple[list[LiquidationTick], int]:
    """Load ticks for *sym* with ts_event in (after_ns, cutoff_ns]."""
    files = sorted(data_dir.glob(f"{sym}_liquidations_*.json"))
    if not files:
        return [], after_ns

    naut_sym = binance_to_nautilus(sym)
    ticks: list[LiquidationTick] = []
    max_ts = after_ns

    with open(files[-1], encoding="utf-8") as f:
        doc = json.load(f)
    for row in doc.get("data") or []:
        try:
            ts_ns = _iso_to_ns(str(row["time"]))
        except (KeyError, TypeError, ValueError):
            continue
        if ts_ns <= after_ns or ts_ns > cutoff_ns:
            continue
        try:
            side = _candlefeed_side(str(row["side"]))
            price = float(row["price"])
            quantity = float(row["quantity"])
            usd_value = float(row["usd_value"])
        except (KeyError, TypeError, ValueError):
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
        if ts_ns > max_ts:
            max_ts = ts_ns

    ticks.sort(key=lambda t: t.ts_init)
    return ticks, max_ts


def sync_bars(
    data_dir: Path,
    sym: str,
    after_ns: int,
    cutoff_ns: int,
) -> tuple[list[Bar], int]:
    """Load 1m bars for *sym* with ts_event (close) in (after_ns, cutoff_ns]."""
    files = sorted(data_dir.glob(f"{sym}_candles_1m_*.json"))
    if not files:
        return [], after_ns

    naut_sym = binance_to_nautilus(sym)
    bar_type = BarType.from_str(f"{naut_sym}-1-MINUTE-LAST-EXTERNAL")
    bars: list[Bar] = []
    max_ts = after_ns

    with open(files[-1], encoding="utf-8") as f:
        doc = json.load(f)
    for row in doc.get("data") or []:
        try:
            bar_open_ns = _iso_to_ns(str(row["time"]))
            ts_event = bar_open_ns + 60_000_000_000
        except (KeyError, TypeError, ValueError):
            continue
        if ts_event <= after_ns or ts_event > cutoff_ns:
            continue
        try:
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
        except (KeyError, TypeError, ValueError):
            continue
        if ts_event > max_ts:
            max_ts = ts_event

    bars.sort(key=lambda b: b.ts_init)
    return bars, max_ts


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Incremental sync of CandleFeed data to catalog")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--catalog", type=Path, default=None)
    p.add_argument(
        "--hours", type=float, default=None,
        help="Force re-sync last N hours (ignores watermark for that window)",
    )
    p.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    if not data_dir.is_dir():
        print(f"Data dir not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    catalog = get_catalog(args.catalog)
    catalog_path = Path(catalog.path)
    state = _load_state(catalog_path)

    now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    symbols = tuple(s.upper() for s in args.symbols)

    total_ticks = 0
    total_bars = 0
    new_state = dict(state)

    for sym in symbols:
        # Determine watermarks
        if args.hours is not None:
            force_after_ns = now_ns - int(args.hours * 3600 * 1_000_000_000)
            tick_after = min(state.get(_watermark_key(sym, "ticks"), 0), force_after_ns)
            bar_after = min(state.get(_watermark_key(sym, "bars"), 0), force_after_ns)
        else:
            tick_after = state.get(_watermark_key(sym, "ticks"), 0)
            bar_after = state.get(_watermark_key(sym, "bars"), 0)

        ticks, new_tick_wm = sync_ticks(data_dir, sym, tick_after, now_ns)
        bars, new_bar_wm = sync_bars(data_dir, sym, bar_after, now_ns)

        tick_after_dt = datetime.fromtimestamp(tick_after / 1e9, tz=timezone.utc).isoformat()
        print(
            f"{sym}: {len(ticks)} new ticks (after {tick_after_dt}), "
            f"{len(bars)} new bars"
        )

        if not args.dry_run:
            if ticks:
                catalog.write_data(ticks)
            if bars:
                catalog.write_data(bars)
            if new_tick_wm > tick_after:
                new_state[_watermark_key(sym, "ticks")] = new_tick_wm
            if new_bar_wm > bar_after:
                new_state[_watermark_key(sym, "bars")] = new_bar_wm

        total_ticks += len(ticks)
        total_bars += len(bars)

    if not args.dry_run and new_state != state:
        _save_state(catalog_path, new_state)
        print(f"Watermarks updated → {catalog_path / SYNC_STATE_FILE}")

    ts = datetime.now(timezone.utc).isoformat()
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}Sync done at {ts}: {total_ticks} ticks, {total_bars} bars appended")


if __name__ == "__main__":
    main()
