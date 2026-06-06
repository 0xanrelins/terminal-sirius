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


def seconds_until_window_end(ts: int | None = None) -> float:
    """Seconds until the current 15m window closes (next UTC-aligned boundary)."""
    t = int(time.time()) if ts is None else ts
    return float(window_start(t) + WINDOW_SEC - t)


def slug_for_series(series: str, ts: int | None = None) -> str:
    return f"{series}-{window_start(ts)}"


def bet_window_slug(series: str, liq_bar_open: int) -> str:
    """Polymarket slug for the 15m window after a liq accumulation bar (bet target)."""
    return f"{series}-{liq_bar_open + WINDOW_SEC}"


def active_rolling_slugs(series: str, ts: int | None = None) -> tuple[str, str]:
    """(current wall-clock slug, next-window slug) for Polymarket DataClient subscribe."""
    t = int(time.time()) if ts is None else ts
    current = slug_for_series(series, ts=t)
    next_open = window_start(t) + WINDOW_SEC
    return current, f"{series}-{next_open}"


def series_symbol(series: str) -> str:
    return f"{series}.POLYMARKET"


def parse_series_from_slug(slug: str) -> str | None:
    """Extract series prefix from a full slug, or None if not a rolling 15m slug."""
    for preset in PRESET_15M_SERIES:
        prefix = preset["series"]
        if slug == prefix or slug.startswith(f"{prefix}-"):
            return prefix
    return None


def parse_window_epoch_from_slug(slug: str) -> int | None:
    """15m window open (UTC epoch sec) from slug suffix, e.g. btc-updown-15m-1778931900."""
    if not slug:
        return None
    tail = slug.rsplit("-", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return None
