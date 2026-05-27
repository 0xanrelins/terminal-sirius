"""Blocking Polymarket helpers for Nautilus strategy thread."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from adapters.polymarket.gamma import get_token_ids
from adapters.polymarket.rolling import slug_for_series
from nautilus_bridge.strategy_runtime import get_main_loop
from simulation.config import Side
from simulation.pricing import resolve_entry_price
from simulation.sizing import compute_bet, compute_live_market_usd, fetch_min_order_size


@dataclass(frozen=True)
class OpenQuote:
    slug: str
    token_id: str
    entry_price: float
    yes_price: float | None
    price_source: str
    min_shares: float
    shares: float
    cost_usd: float


def _run_async(coro, *, timeout: float = 30):
    loop = get_main_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


def quote_for_bet(
    *,
    poly_series: str,
    candle_open: int,
    side: Side,
    min_shares_default: float,
    min_usd: float,
    for_live: bool = False,
    backtest_entry: float | None = None,
) -> OpenQuote | None:
    slug = slug_for_series(poly_series, ts=candle_open)
    if backtest_entry is not None:
        entry_price = backtest_entry
        price_source = "backtest_catalog"
        yes_price = entry_price if side == "long" else (1.0 - entry_price)
    else:
        quote = _run_async(resolve_entry_price(slug, side))
        if quote is None:
            return None
        entry_price = quote.entry_price
        price_source = quote.source
        yes_price = quote.yes_price
        if for_live:
            entry_price = quote.entry_price
            price_source = "nautilus_cache_live"
    token_id: str | None = None
    min_shares = min_shares_default
    try:
        info = _run_async(get_token_ids(slug))
        if info:
            token = info.get("yes") if side == "long" else info.get("no")
            if token:
                token_id = str(token)
                min_shares = _run_async(
                    fetch_min_order_size(token_id, min_shares_default)
                )
    except Exception:
        pass
    if not token_id:
        return None
    try:
        if for_live:
            shares, cost_usd = compute_live_market_usd(entry_price, min_shares)
        else:
            shares, cost_usd = compute_bet(entry_price, min_shares, min_usd)
    except ValueError:
        return None
    return OpenQuote(
        slug=slug,
        token_id=token_id,
        entry_price=entry_price,
        yes_price=yes_price,
        price_source=price_source,
        min_shares=min_shares,
        shares=shares,
        cost_usd=cost_usd,
    )


async def load_restore_state(mode: str) -> "RestoreState":
    from strategies.liq_poly_config import RestoreBet, RestoreState
    import db
    import time

    since = int(time.time()) - 7 * 86400
    if mode == "live":
        await db.repair_stuck_live_cycles()
        signaled = await db.get_live_signaled_keys(since)
        cycles = await db.get_open_live_cycles()
        active = {}
        for c in cycles:
            side = c.get("side") or "long"
            active[(c["asset"], side)] = int(c["id"])
        bets_raw = await db.get_open_live_bets_for_cycles()
    else:
        await db.repair_stuck_simulation_cycles()
        signaled = await db.get_simulation_signaled_keys(since)
        cycles = await db.get_open_simulation_cycles()
        active = {}
        for c in cycles:
            side = c.get("side") or "long"
            active[(c["asset"], side)] = int(c["id"])
        bets_raw = await db.get_open_bets_for_cycles()

    bets = [
        RestoreBet(
            bet_id=int(b["id"]),
            binance_symbol=b["binance_symbol"],
            side=b.get("side") or "long",
            leg=int(b["leg"]),
            asset=b["asset"],
            cycle_id=int(b["cycle_id"]),
            candle_open=int(b["candle_open"]),
            poly_series=b["poly_series"],
            entry_price=float(b["entry_price"]),
            shares=float(b["shares"]),
            cost_usd=float(b["cost_usd"]),
            order_id=b.get("order_id"),
        )
        for b in bets_raw
    ]
    return RestoreState(
        signaled=[(s, int(bo), str(sd)) for s, bo, sd in signaled],
        active_cycles=active,
        open_bets=bets,
    )
