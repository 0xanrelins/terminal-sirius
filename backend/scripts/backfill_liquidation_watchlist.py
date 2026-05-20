#!/usr/bin/env python3
"""
One-shot: fill liquidation_watchlist_events from existing liquidation_events.

Run after migration (trigger only applies to new inserts). From backend/:

  python scripts/backfill_liquidation_watchlist.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")
load_dotenv(_BACKEND_ROOT / ".env")

SQL = """
INSERT INTO liquidation_watchlist_events (
    trade_id, symbol, side, notional, time, received_at
)
SELECT
    e.trade_id,
    (e.payload->'o'->>'s') || '-PERP.BINANCE',
    COALESCE(e.payload->'o'->>'S', ''),
    ((e.payload->'o'->>'ap')::double precision
     * (e.payload->'o'->>'z')::double precision),
    ((e.payload->'o'->>'T')::bigint / 1000),
    e.received_at
FROM liquidation_events e
WHERE (e.payload->'o'->>'s') IN (
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'XRPUSDT',
    'HYPEUSDT', 'BNBUSDT'
)
  AND e.payload->'o'->>'ap' IS NOT NULL
  AND e.payload->'o'->>'z' IS NOT NULL
  AND e.payload->'o'->>'T' IS NOT NULL
ON CONFLICT (trade_id) DO NOTHING;
"""


async def main() -> None:
    import db

    await db.init()
    try:
        n = await db.pool().execute(SQL)
        print(f"[backfill] watchlist rows affected: {n}", flush=True)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
