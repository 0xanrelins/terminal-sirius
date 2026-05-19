#!/usr/bin/env python3
"""
Load NDJSON written by `record_binance_liquidations.py` into `liquidation_events`.

Each line should be JSON with a `raw` object (WS payload) or be the raw object itself.
Every `forceOrder` item inside `raw` is inserted with ON CONFLICT DO NOTHING on trade_id.

Usage (from backend/):
  python scripts/import_liquidation_ndjson.py data/liq_raw/2026-05-18.ndjson
  python scripts/import_liquidation_ndjson.py data/liq_raw/ --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(_BACKEND_ROOT / ".env")


def iter_force_order_items(envelope: dict) -> list[dict]:
    data = envelope.get("data", envelope)
    events = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for item in events:
        if isinstance(item, dict) and item.get("e") == "forceOrder":
            out.append(item)
    return out


def _collect_ndjson_paths(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        paths = sorted(target.glob("*.ndjson"))
        if not paths:
            raise SystemExit(f"No .ndjson files under {target}")
        return paths
    raise SystemExit(f"Not a file or directory: {target}")


async def import_paths(paths: list[Path], *, dry_run: bool) -> tuple[int, int]:
    import db
    from liquidations import force_order_trade_id

    if not dry_run:
        await db.init()
    inserted = 0
    seen_lines = 0
    try:
        for path in paths:
            print(f"[import] {path}", flush=True)
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    seen_lines += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError as e:
                        print(f"[import] skip bad JSON line {seen_lines}: {e}", flush=True)
                        continue
                    raw = rec.get("raw", rec)
                    if not isinstance(raw, dict):
                        continue
                    for item in iter_force_order_items(raw):
                        tid = force_order_trade_id(item)
                        if dry_run:
                            inserted += 1
                            continue
                        if await db.insert_liquidation_event(tid, item):
                            inserted += 1
    finally:
        if not dry_run:
            await db.close()
    return seen_lines, inserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import recorder NDJSON into liquidation_events",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="NDJSON file(s) or directory containing *.ndjson",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse only; do not write to the database",
    )
    args = parser.parse_args()

    all_paths: list[Path] = []
    for p in args.paths:
        p = p.expanduser().resolve()
        all_paths.extend(_collect_ndjson_paths(p))
    # Stable order, no duplicate files if CLI repeats paths
    all_paths = sorted({p.resolve() for p in all_paths})

    lines, ins = asyncio.run(import_paths(all_paths, dry_run=args.dry_run))
    mode = "rows that would insert" if args.dry_run else "new rows inserted"
    print(f"[import] done: {lines} lines processed, {ins} {mode}", flush=True)


if __name__ == "__main__":
    main()
