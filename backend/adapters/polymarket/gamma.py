"""
Polymarket Gamma API client.

Gamma API is the REST layer for market metadata (questions, slugs, token IDs, volumes).
CLOB API is the order-book / price-feed layer.
"""
import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"


async def get_market_by_slug(slug: str) -> dict | None:
    """Return the market record for a single slug, or None if not found."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{GAMMA_BASE}/markets", params={"slug": slug})
        resp.raise_for_status()
        data = resp.json()
    return data[0] if data else None


async def search_markets(q: str, limit: int = 20) -> list[dict]:
    """Search active markets by keyword. Returns a simplified list."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        # Gamma supports ?search= for full-text on the question field
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


async def get_token_ids(slug: str) -> dict | None:
    """
    Return {"yes": tokenId, "no": tokenId, "question": "..."} for a slug.
    Returns None if market not found or tokens unavailable.
    """
    market = await get_market_by_slug(slug)
    if not market:
        return None

    tokens = market.get("tokens", [])
    yes_id = next((t["tokenId"] for t in tokens if t.get("outcome") == "Yes"), None)
    no_id  = next((t["tokenId"] for t in tokens if t.get("outcome") == "No"), None)

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
    tokens = m.get("tokens", [])
    yes_price = next(
        (float(t.get("price", 0)) for t in tokens if t.get("outcome") == "Yes"), None
    )
    return {
        "slug": m.get("slug", ""),
        "question": m.get("question", ""),
        "yes_price": yes_price,
        "volume": float(m.get("volume", 0) or 0),
        "active": m.get("active", False),
    }
