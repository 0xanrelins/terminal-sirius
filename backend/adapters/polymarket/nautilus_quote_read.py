"""Read latest Polymarket QuoteTick from Nautilus cache (TradingNode)."""
from __future__ import annotations

from adapters.polymarket.quote_registry import SlugQuoteBook, update_slug_quote
from node import get_trading_node


def _tick_prices(tick) -> tuple[float, float]:
    bid = float(tick.bid_price.as_double()) if hasattr(tick.bid_price, "as_double") else float(tick.bid_price)
    ask = float(tick.ask_price.as_double()) if hasattr(tick.ask_price, "as_double") else float(tick.ask_price)
    return bid, ask


def book_from_nautilus_cache(slug: str, *, yes_iid: str, no_iid: str | None = None) -> SlugQuoteBook | None:
    """Pull latest quotes from the live TradingNode cache for slug instruments."""
    from nautilus_trader.model.identifiers import InstrumentId

    node = get_trading_node()
    if node is None:
        return None
    cache = node.cache
    book = SlugQuoteBook()
    yes_tick = cache.quote_tick(InstrumentId.from_str(yes_iid))
    if yes_tick is not None:
        book.yes_bid, book.yes_ask = _tick_prices(yes_tick)
        book.ts_ms = int(yes_tick.ts_event // 1_000_000)
    if no_iid:
        no_tick = cache.quote_tick(InstrumentId.from_str(no_iid))
        if no_tick is not None:
            book.no_bid, book.no_ask = _tick_prices(no_tick)
            if book.ts_ms == 0 and no_tick is not None:
                book.ts_ms = int(no_tick.ts_event // 1_000_000)
    if book.yes_mid is None and book.no_mid is None:
        return None
    update_slug_quote(
        slug,
        token="yes",
        bid=book.yes_bid or 0.0,
        ask=book.yes_ask or 0.0,
        ts_ms=book.ts_ms,
    )
    if no_iid and (book.no_bid or book.no_ask):
        update_slug_quote(
            slug,
            token="no",
            bid=book.no_bid or 0.0,
            ask=book.no_ask or 0.0,
            ts_ms=book.ts_ms,
        )
    return book
