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


def _level_price(level) -> float:
    if isinstance(level, dict):
        return float(level.get("price", 0) or 0)
    if isinstance(level, (list, tuple)) and level:
        return float(level[0])
    return 0.0


async def fetch_clob_book(token_id: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{CLOB_BASE}/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


async def fetch_clob_best_bid(token_id: str) -> float | None:
    """Highest bid on the book."""
    data = await fetch_clob_book(token_id)
    if not data:
        return None
    bids = data.get("bids") or []
    if not bids:
        return None
    price = _level_price(bids[0])
    if 0 < price < 1:
        return round(price, 4)
    return None


async def fetch_clob_best_ask(token_id: str) -> float | None:
    """Lowest ask on the book — simulated market-buy fill price."""
    data = await fetch_clob_book(token_id)
    if not data:
        return None
    asks = data.get("asks") or []
    if not asks:
        return None
    price = _level_price(asks[0])
    if 0 < price < 1:
        return round(price, 4)
    return None


def is_credible_clob_book(best_bid: float | None, best_ask: float | None) -> bool:
    """
    Pre-open 15m markets often have mirror junk (bid 1–3¢, ask 97–99¢).
    Only trust CLOB when bid/ask look like real liquidity.
    """
    if best_bid is None or best_ask is None:
        return False
    if best_bid <= 0.15 and best_ask >= 0.85:
        return False
    if best_ask - best_bid > 0.30:
        return False
    if not (0.05 < best_ask < 0.95 and 0.05 < best_bid < 0.95):
        return False
    return True


async def fetch_clob_mid_price(token_id: str) -> float | None:
    """Mid from CLOB book; only when both bid and ask exist (no empty-book 0.5 guess)."""
    data = await fetch_clob_book(token_id)
    if not data:
        return None
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = _level_price(bids[0])
    best_ask = _level_price(asks[0])
    if best_bid and best_ask:
        mid = (best_bid + best_ask) / 2
        if 0 < mid < 1:
            return round(mid, 4)
    return None


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
