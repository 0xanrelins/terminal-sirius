"""Polymarket-compatible bet sizing (min USD + min shares)."""
from __future__ import annotations

import math

import httpx

CLOB_BASE = "https://clob.polymarket.com"
_min_shares_cache: dict[str, float] = {}


def compute_bet(
    entry_price: float,
    min_shares: float,
    min_usd: float,
) -> tuple[float, float]:
    if entry_price <= 0 or entry_price >= 1:
        raise ValueError(f"entry_price must be in (0, 1), got {entry_price}")
    shares = max(min_shares, math.ceil(min_usd / entry_price))
    cost_usd = round(shares * entry_price, 4)
    return shares, cost_usd


def pnl_for_outcome(shares: float, cost_usd: float, won: bool) -> float:
    if won:
        return round(shares * 1.0 - cost_usd, 4)
    return round(-cost_usd, 4)


async def fetch_min_order_size(token_id: str, fallback: float) -> float:
    if token_id in _min_shares_cache:
        return _min_shares_cache[token_id]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{CLOB_BASE}/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
            mos = float(data.get("min_order_size") or fallback)
            _min_shares_cache[token_id] = mos
            return mos
    except Exception:
        return fallback
