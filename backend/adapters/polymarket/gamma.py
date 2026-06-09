"""
Polymarket Gamma API client.

Gamma API is the REST layer for market metadata (questions, slugs, token IDs, volumes).
CLOB API is the order-book / price-feed layer.
"""
from __future__ import annotations

import json

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"

_gamma_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    """Reuse one AsyncClient — rotate used to spawn a client per request (EMFILE risk)."""
    global _gamma_client
    if _gamma_client is None:
        _gamma_client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=3),
        )
    return _gamma_client


async def aclose_gamma_client() -> None:
    global _gamma_client
    if _gamma_client is not None:
        await _gamma_client.aclose()
        _gamma_client = None


async def get_market_by_slug(slug: str) -> dict | None:
    """Return market record for a slug (active first, then closed fallback)."""
    client = _client()
    resp = await client.get(f"{GAMMA_BASE}/markets", params={"slug": slug})
    resp.raise_for_status()
    data = resp.json()
    if data:
        return data[0]

    # Resolved 15m markets often disappear from the default active query surface.
    # Retry against the closed slice so post-expiry lookups still resolve metadata.
    resp_closed = await client.get(
        f"{GAMMA_BASE}/markets",
        params={"slug": slug, "closed": "true"},
    )
    resp_closed.raise_for_status()
    closed_data = resp_closed.json()
    return closed_data[0] if closed_data else None


async def search_markets(q: str, limit: int = 20) -> list[dict]:
    """Search active markets by keyword. Returns a simplified list."""
    client = _client()
    resp = await client.get(
        f"{GAMMA_BASE}/markets",
        params={"active": "true", "limit": str(limit * 3), "search": q},
    )
    resp.raise_for_status()
    markets = resp.json()

    # Gamma may ignore the param; filter client-side as fallback
    if not any(q.lower() in m.get("question", "").lower() for m in markets):
        markets = [m for m in markets if q.lower() in m.get("question", "").lower()]

    return [_slim(m) for m in markets[:limit]]


def _parse_json_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []
    return []


def _token_ids_from_market(market: dict) -> tuple[str | None, str | None]:
    """Return (up_token, down_token) for Up/Down or Yes/No outcome markets."""
    tokens = market.get("tokens") or []
    if tokens:
        up = next(
            (t["tokenId"] for t in tokens if t.get("outcome") in ("Up", "Yes")),
            None,
        )
        down = next(
            (t["tokenId"] for t in tokens if t.get("outcome") in ("Down", "No")),
            None,
        )
        if up:
            return up, down

    outcomes = _parse_json_list(market.get("outcomes"))
    clob_ids = _parse_json_list(market.get("clobTokenIds"))
    if not clob_ids:
        return None, None

    up_idx = next(
        (i for i, o in enumerate(outcomes) if str(o).lower() in ("up", "yes")),
        0,
    )
    down_idx = next(
        (i for i, o in enumerate(outcomes) if str(o).lower() in ("down", "no")),
        1 if len(clob_ids) > 1 else None,
    )
    up_id = clob_ids[up_idx] if up_idx is not None and up_idx < len(clob_ids) else clob_ids[0]
    down_id = (
        clob_ids[down_idx]
        if down_idx is not None and down_idx < len(clob_ids)
        else (clob_ids[1] if len(clob_ids) > 1 else None)
    )
    return str(up_id), str(down_id) if down_id is not None else None


def outcome_prices_from_market(market: dict) -> tuple[float | None, float | None]:
    """Return (up_yes_price, down_no_price) from a Gamma market record."""
    tokens = market.get("tokens") or []
    if tokens:
        up_p = down_p = None
        for t in tokens:
            outcome = t.get("outcome")
            p = float(t.get("price", 0) or 0)
            if not (0 < p < 1):
                continue
            if outcome in ("Yes", "Up"):
                up_p = p
            elif outcome in ("No", "Down"):
                down_p = p
        if up_p is not None or down_p is not None:
            return up_p, down_p

    outcomes = _parse_json_list(market.get("outcomes"))
    prices = _parse_json_list(market.get("outcomePrices"))
    up_p = down_p = None
    for i, o in enumerate(outcomes):
        if i >= len(prices):
            break
        p = float(prices[i])
        if not (0 < p < 1):
            continue
        label = str(o).lower()
        if label in ("up", "yes"):
            up_p = p
        elif label in ("down", "no"):
            down_p = p
    return up_p, down_p


def yes_price_from_market(market: dict) -> float | None:
    """Extract Up/Yes outcome price (0–1) from a Gamma market record."""
    up_p, _ = outcome_prices_from_market(market)
    return up_p


async def get_yes_price_for_slug(slug: str) -> float | None:
    """UP/YES price for a specific market slug (target window), not the active WS feed."""
    market = await get_market_by_slug(slug)
    if not market:
        return None
    return yes_price_from_market(market)


async def get_token_ids(slug: str) -> dict | None:
    """
    Return {"yes": up_token_id, "no": down_token_id, "question": "..."} for a slug.
    The "yes" key holds the Up/Yes outcome token (CLOB asset id).
    Returns None if market not found or tokens unavailable.
    """
    market = await get_market_by_slug(slug)
    if not market:
        return None

    yes_id, no_id = _token_ids_from_market(market)
    if not yes_id:
        return None

    return {
        "yes": yes_id,
        "no": no_id,
        "question": market.get("question", slug),
        "volume": float(market.get("volume", 0) or 0),
        "slug": slug,
    }


def _slim(m: dict) -> dict:
    yes_price = yes_price_from_market(m)
    return {
        "slug": m.get("slug", ""),
        "question": m.get("question", ""),
        "yes_price": yes_price,
        "volume": float(m.get("volume", 0) or 0),
        "active": m.get("active", False),
    }
