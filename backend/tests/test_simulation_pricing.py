"""Tests for Nautilus-cache-only entry price resolution."""
import asyncio

from adapters.polymarket.quote_registry import clear_quotes, get_slug_quotes, update_slug_quote
from simulation.pricing import resolve_entry_price
from simulation.sizing import is_credible_clob_book


def setup_function() -> None:
    clear_quotes()


def test_junk_clob_mirror_book() -> None:
    assert is_credible_clob_book(0.01, 0.99) is False
    assert is_credible_clob_book(0.48, 0.52) is True


def test_resolve_long_from_registry() -> None:
    slug = "btc-updown-15m-test"
    update_slug_quote(slug, token="yes", bid=0.48, ask=0.52)
    update_slug_quote(slug, token="no", bid=0.47, ask=0.51)

    async def _run():
        return await resolve_entry_price(slug, "long")

    quote = asyncio.run(_run())
    assert quote is not None
    assert quote.entry_price == 0.52
    assert quote.source == "nautilus_cache"
    book = get_slug_quotes(slug)
    assert book is not None
    assert book.yes_mid is not None


def test_resolve_short_uses_no_ask() -> None:
    slug = "eth-updown-15m-test"
    update_slug_quote(slug, token="yes", bid=0.03, ask=0.04)
    update_slug_quote(slug, token="no", bid=0.95, ask=0.97)

    async def _run():
        return await resolve_entry_price(slug, "short")

    quote = asyncio.run(_run())
    assert quote is not None
    assert quote.entry_price == 0.97
    assert quote.source == "nautilus_cache"


def test_missing_registry_returns_none() -> None:
    async def _run():
        return await resolve_entry_price("unknown-slug", "long")

    assert asyncio.run(_run()) is None
