"""
Strategy state catch-up (missed liq signals / open bet settlements).

Not Nautilus ExecEngine order reconciliation — that is handled by the
Polymarket ExecutionClient + LiveExecEngine open-check interval.
"""
from __future__ import annotations

import time

from adapters.polymarket.rolling import WINDOW_SEC
from engines.liq_poly_runner import LiqPolyRunner
from klines import fetch_klines
from liquidations import fetch_liquidation_bars, get_memory_bars
from strategies.liq_poly_config import LiqPolyRuntimeConfig


async def _current_bar_totals(
    runner: LiqPolyRunner, symbol: str, bar_open: int
) -> tuple[float, float]:
    lk = (symbol, bar_open)
    long_t = runner._bar_long.get(lk, 0.0)
    short_t = runner._bar_short.get(lk, 0.0)
    for b in get_memory_bars(symbol, "15m", limit=8):
        if int(b["time"]) == bar_open:
            long_t = max(long_t, float(b["long"]))
            short_t = max(short_t, float(b["short"]))
    bars = await fetch_liquidation_bars(symbol, "15m", limit=1)
    if bars and int(bars[-1]["time"]) == bar_open:
        long_t = max(long_t, float(bars[-1]["long"]))
        short_t = max(short_t, float(bars[-1]["short"]))
    runner._bar_long[lk] = long_t
    runner._bar_short[lk] = short_t
    return long_t, short_t


async def catchup_bar_cmds(
    runner: LiqPolyRunner, cfg: LiqPolyRuntimeConfig
) -> list[dict]:
    cmds: list[dict] = []
    for _asset, meta in cfg.assets.items():
        symbol = meta["binance_symbol"]
        bars = await fetch_liquidation_bars(symbol, "15m", limit=1)
        if not bars:
            continue
        bar_open = int(bars[-1]["time"])
        long_t, short_t = await _current_bar_totals(runner, symbol, bar_open)
        signal_ts = bar_open + WINDOW_SEC
        cmds.extend(
            runner.on_liq_bar(
                symbol=symbol,
                bar_open=bar_open,
                long_total=long_t,
                short_total=short_t,
                signal_ts=signal_ts,
            )
        )
    return cmds


async def catchup_settlement_cmds(
    runner: LiqPolyRunner, _cfg: LiqPolyRuntimeConfig
) -> list[dict]:
    cmds: list[dict] = []
    now = int(time.time())
    for bet in runner.iter_open_bets():
        bar_open = bet.candle_open
        if now < bar_open + WINDOW_SEC:
            continue
        bars = await fetch_klines(
            bet.binance_symbol,
            "15m",
            limit=5,
            before=bar_open + WINDOW_SEC + 60,
        )
        target = next((b for b in bars if int(b["time"]) == bar_open), None)
        if not target:
            continue
        cmds.extend(
            runner.on_bar_close(
                symbol=bet.binance_symbol,
                bar_open=bar_open,
                open_p=float(target["open"]),
                close_p=float(target["close"]),
            )
        )
    return cmds
