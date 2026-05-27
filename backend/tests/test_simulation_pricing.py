"""Tests for simulation entry price resolution."""
import asyncio
import math
from unittest.mock import patch

from adapters.polymarket.gamma import outcome_prices_from_market
from simulation.pricing import _pick_entry, is_gamma_placeholder, resolve_entry_price
from simulation.sizing import is_credible_clob_book

_ABS = 0.001


def test_outcome_prices_from_market() -> None:
    m = {"outcomes": '["Up","Down"]', "outcomePrices": '["0.035","0.965"]'}
    up, down = outcome_prices_from_market(m)
    assert math.isclose(up, 0.035, abs_tol=_ABS)
    assert math.isclose(down, 0.965, abs_tol=_ABS)


def test_placeholder_detection() -> None:
    assert is_gamma_placeholder(0.5) is True
    assert is_gamma_placeholder(0.965) is False


def test_junk_clob_mirror_book() -> None:
    assert is_credible_clob_book(0.01, 0.99) is False
    assert is_credible_clob_book(0.48, 0.52) is True


def test_pick_long_uses_gamma_over_junk_clob() -> None:
    q = _pick_entry(
        side="long",
        up_gamma=0.505,
        down_gamma=0.495,
        up_bid=0.01,
        up_ask=0.99,
        down_bid=0.01,
        down_ask=0.99,
    )
    assert q is not None
    assert math.isclose(q.entry_price, 0.505, abs_tol=_ABS)
    assert q.source == "gamma"


def test_pick_short_asymmetric_gamma() -> None:
    q = _pick_entry(
        side="short",
        up_gamma=0.035,
        down_gamma=0.965,
        up_bid=None,
        up_ask=None,
        down_bid=None,
        down_ask=None,
    )
    assert q is not None
    assert math.isclose(q.entry_price, 0.965, abs_tol=_ABS)
    assert q.source == "gamma"


async def _resolve_short_uses_down_gamma() -> None:
    async def fake_tokens(slug: str):
        return {"yes": "yes-tok", "no": "no-tok"}

    async def fake_market(slug: str):
        return {
            "outcomes": '["Up","Down"]',
            "outcomePrices": '["0.035","0.965"]',
        }

    async def junk_ask(token_id: str):
        return 0.99

    async def junk_bid(token_id: str):
        return 0.01

    with (
        patch("simulation.pricing.get_token_ids", fake_tokens),
        patch("simulation.pricing.get_market_by_slug", fake_market),
        patch("simulation.pricing.fetch_clob_best_ask", junk_ask),
        patch("simulation.pricing.fetch_clob_best_bid", junk_bid),
    ):
        quote = await resolve_entry_price("eth-updown-15m-test", "short")
    assert quote is not None
    assert math.isclose(quote.entry_price, 0.965, abs_tol=_ABS)
    assert quote.source == "gamma"


def test_resolve_short_uses_down_gamma() -> None:
    asyncio.run(_resolve_short_uses_down_gamma())
