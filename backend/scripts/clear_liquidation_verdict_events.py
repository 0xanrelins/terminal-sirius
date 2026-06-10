#!/usr/bin/env python3
"""Truncate liquidation_verdict_events (fresh start after threshold change)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402


async def main() -> None:
    await db.init()
    deleted = await db.clear_liquidation_verdict_events()
    print(f"Deleted {deleted} liquidation_verdict_events row(s).")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
