"""Resolve Polymarket entry prices from Nautilus quote cache only (no Gamma/CLOB REST)."""
from __future__ import annotations

from dataclasses import dataclass

from adapters.polymarket.nautilus_quote_read import book_from_nautilus_cache
from adapters.polymarket.quote_registry import (
    SlugQuoteBook,
    get_slug_instruments,
    get_slug_quotes,
)
from simulation.config import Side
from simulation.sizing import is_credible_clob_book

# Registry entries older than this are refreshed from Nautilus cache when possible.
MAX_QUOTE_AGE_MS = 120_000


@dataclass(frozen=True)
class EntryPriceQuote:
    entry_price: float
    yes_price: float | None
    no_price: float | None
    source: str


def _book_stale(book: SlugQuoteBook) -> bool:
    if book.ts_ms <= 0:
        return True
    import time

    return (int(time.time() * 1000) - book.ts_ms) > MAX_QUOTE_AGE_MS


def _pick_entry_from_book(*, side: Side, book: SlugQuoteBook) -> EntryPriceQuote | None:
    if side == "long":
        bid, ask = book.yes_bid, book.yes_ask
    else:
        bid, ask = book.no_bid, book.no_ask

    if not is_credible_clob_book(bid, ask) or ask is None:
        return None
    return EntryPriceQuote(
        entry_price=ask,
        yes_price=book.yes_mid,
        no_price=book.no_mid,
        source="nautilus_cache",
    )


async def _hydrate_book(slug: str) -> SlugQuoteBook | None:
    book = get_slug_quotes(slug)
    if book is not None and not _book_stale(book):
        return book
    iids = get_slug_instruments(slug)
    if not iids:
        return book
    yes_iid, no_iid = iids
    try:
        refreshed = book_from_nautilus_cache(slug, yes_iid=yes_iid, no_iid=no_iid)
        return refreshed or book
    except Exception:
        return book


async def resolve_entry_price(slug: str, side: Side) -> EntryPriceQuote | None:
    """
    Entry price from Nautilus quote registry / TradingNode cache only.

    Requires the slug to be subscribed via Polymarket DataClient + quote bridge.
  """
    book = await _hydrate_book(slug)
    if book is None:
        return None
    return _pick_entry_from_book(side=side, book=book)


# Kept for unit tests documenting placeholder detection (Gamma no longer used for entry).
PLACEHOLDER_LOW = 0.45
PLACEHOLDER_HIGH = 0.55


def is_gamma_placeholder(price: float | None) -> bool:
    if price is None:
        return False
    return PLACEHOLDER_LOW <= price <= PLACEHOLDER_HIGH
