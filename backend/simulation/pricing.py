"""Resolve realistic Polymarket entry prices for paper simulation."""
from __future__ import annotations

from dataclasses import dataclass

from adapters.polymarket.gamma import (
    get_market_by_slug,
    get_token_ids,
    outcome_prices_from_market,
)
from simulation.config import Side
from simulation.sizing import (
    fetch_clob_best_ask,
    fetch_clob_best_bid,
    is_credible_clob_book,
)

# Gamma 50/50 on brand-new windows is imperfect but far better than junk CLOB asks.
PLACEHOLDER_LOW = 0.45
PLACEHOLDER_HIGH = 0.55


def is_gamma_placeholder(price: float | None) -> bool:
    if price is None:
        return False
    return PLACEHOLDER_LOW <= price <= PLACEHOLDER_HIGH


def _valid_gamma(price: float | None) -> bool:
    return price is not None and 0.01 < price < 0.99


@dataclass(frozen=True)
class EntryPriceQuote:
    entry_price: float
    yes_price: float | None
    no_price: float | None
    source: str


def _pick_entry(
    *,
    side: Side,
    up_gamma: float | None,
    down_gamma: float | None,
    up_bid: float | None,
    up_ask: float | None,
    down_bid: float | None,
    down_ask: float | None,
) -> EntryPriceQuote | None:
    if side == "long":
        gamma_p, bid, ask = up_gamma, up_bid, up_ask
    else:
        gamma_p, bid, ask = down_gamma, down_bid, down_ask

    cred_clob = is_credible_clob_book(bid, ask)

    # 1) Gamma — default for 15m up/down (includes ~50/50 pre-open).
    if _valid_gamma(gamma_p):
        if (
            cred_clob
            and not is_gamma_placeholder(gamma_p)
            and ask is not None
            and abs(ask - gamma_p) <= 0.12
        ):
            entry = min(ask, gamma_p)
            src = "clob_ask" if entry == ask else "gamma"
        else:
            entry, src = gamma_p, "gamma"
        return EntryPriceQuote(entry, up_gamma, down_gamma, src)

    # 2) CLOB ask only when book is credible (no mirror 1¢/99¢).
    if cred_clob and ask is not None:
        return EntryPriceQuote(ask, up_gamma, down_gamma, "clob_ask")

    return None


async def resolve_entry_price(slug: str, side: Side) -> EntryPriceQuote | None:
    """
    Price to buy the outcome we are simulating.

    Priority:
      1. Gamma outcome price (even ~50/50 pre-open — beats junk CLOB)
      2. CLOB best ask only on a credible book (tight bid/ask, not 1¢ vs 99¢)
    """
    info = await get_token_ids(slug)
    market = await get_market_by_slug(slug)
    up_gamma = down_gamma = None
    if market:
        up_gamma, down_gamma = outcome_prices_from_market(market)

    up_bid = up_ask = down_bid = down_ask = None
    if info:
        if info.get("yes"):
            up_bid = await fetch_clob_best_bid(info["yes"])
            up_ask = await fetch_clob_best_ask(info["yes"])
        if info.get("no"):
            down_bid = await fetch_clob_best_bid(info["no"])
            down_ask = await fetch_clob_best_ask(info["no"])

    return _pick_entry(
        side=side,
        up_gamma=up_gamma,
        down_gamma=down_gamma,
        up_bid=up_bid,
        up_ask=up_ask,
        down_bid=down_bid,
        down_ask=down_ask,
    )
