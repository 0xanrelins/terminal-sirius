"""
Rolling 15-minute Polymarket up/down markets.

Slug pattern:  {series}-{window_start_unix}
  e.g. btc-updown-15m-1778931900  (window = 15 min aligned UTC epoch)
"""
import time

WINDOW_SEC = 900  # 15 minutes

# User-configured series (stable id; slug suffix rotates every window).
PRESET_15M_SERIES: tuple[dict[str, str], ...] = (
    {"series": "btc-updown-15m", "label": "BTC", "asset": "BTC"},
    {"series": "eth-updown-15m", "label": "ETH", "asset": "ETH"},
    {"series": "sol-updown-15m", "label": "SOL", "asset": "SOL"},
    {"series": "doge-updown-15m", "label": "DOGE", "asset": "DOGE"},
    {"series": "xrp-updown-15m", "label": "XRP", "asset": "XRP"},
)


def window_start(ts: int | None = None) -> int:
    t = int(time.time()) if ts is None else ts
    return (t // WINDOW_SEC) * WINDOW_SEC


def slug_for_series(series: str, ts: int | None = None) -> str:
    return f"{series}-{window_start(ts)}"


def series_symbol(series: str) -> str:
    return f"{series}.POLYMARKET"


def parse_series_from_slug(slug: str) -> str | None:
    """Extract series prefix from a full slug, or None if not a rolling 15m slug."""
    for preset in PRESET_15M_SERIES:
        prefix = preset["series"]
        if slug == prefix or slug.startswith(f"{prefix}-"):
            return prefix
    return None
