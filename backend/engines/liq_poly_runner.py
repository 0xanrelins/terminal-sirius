"""
Synchronous liq→Poly state machine (shared by LiqPolyStrategy sim + live).

Returns command dicts; strategy executes orders / forwards persist requests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from adapters.polymarket.rolling import WINDOW_SEC
from simulation.config import BINANCE_TO_ASSET, SIDES, Side, bet_window_open
from strategies.liq_poly_config import LiqPolyRuntimeConfig, RestoreBet, RestoreState

Side = Literal["long", "short"]


def _bet_key(binance_symbol: str, side: Side, candle_open: int) -> tuple[str, str, int]:
    return (binance_symbol, side, candle_open)


def _cycle_key(asset: str, side: Side) -> tuple[str, str]:
    return (asset, side)


def _signal_key(symbol: str, bar_open: int, side: Side) -> tuple[str, int, str]:
    return (symbol, bar_open, side)


def _candle_won(side: Side, open_p: float, close_p: float) -> bool:
    if side == "long":
        return close_p >= open_p
    return close_p < open_p


@dataclass
class _OpenBet:
    bet_id: int | None
    cycle_id: int | None
    leg: int
    side: Side
    asset: str
    binance_symbol: str
    poly_series: str
    candle_open: int
    entry_price: float
    shares: float
    cost_usd: float
    order_id: str | None = None
    pending: bool = False


class LiqPolyRunner:
    def __init__(self, cfg: LiqPolyRuntimeConfig) -> None:
        self._cfg = cfg
        self._bar_long: dict[tuple[str, int], float] = {}
        self._bar_short: dict[tuple[str, int], float] = {}
        self._signaled: set[tuple[str, int, str]] = set()
        self._active_cycle: dict[tuple[str, str], int] = {}
        self._open_bets: dict[tuple[str, str, int], _OpenBet] = {}
        self._restore_apply(cfg.restore)

    def _restore_apply(self, restore: RestoreState) -> None:
        for sym, bar_open, side in restore.signaled:
            self._signaled.add(_signal_key(sym, bar_open, side))
        self._active_cycle.update(restore.active_cycles)
        for b in restore.open_bets:
            key = _bet_key(b.binance_symbol, b.side, b.candle_open)
            self._open_bets[key] = _OpenBet(
                bet_id=b.bet_id,
                cycle_id=b.cycle_id,
                leg=b.leg,
                side=b.side,
                asset=b.asset,
                binance_symbol=b.binance_symbol,
                poly_series=b.poly_series,
                candle_open=b.candle_open,
                entry_price=b.entry_price,
                shares=b.shares,
                cost_usd=b.cost_usd,
                order_id=b.order_id,
            )

    def clear_signal_cache(self) -> None:
        self._signaled.clear()

    def on_liq_bar(
        self,
        *,
        symbol: str,
        bar_open: int,
        long_total: float,
        short_total: float,
        signal_ts: int,
    ) -> list[dict]:
        lk = (symbol, bar_open)
        self._bar_long[lk] = long_total
        self._bar_short[lk] = short_total
        if symbol not in BINANCE_TO_ASSET:
            return []
        asset = BINANCE_TO_ASSET[symbol]
        if asset not in self._cfg.assets:
            return []
        cmds: list[dict] = []
        if long_total > 0:
            cmds.extend(
                self._maybe_signal(
                    symbol=symbol,
                    asset=asset,
                    bar_open=bar_open,
                    side="long",
                    total=long_total,
                    signal_ts=signal_ts,
                )
            )
        if short_total > 0:
            cmds.extend(
                self._maybe_signal(
                    symbol=symbol,
                    asset=asset,
                    bar_open=bar_open,
                    side="short",
                    total=short_total,
                    signal_ts=signal_ts,
                )
            )
        return cmds

    def on_bar_close(
        self, *, symbol: str, bar_open: int, open_p: float, close_p: float
    ) -> list[dict]:
        cmds: list[dict] = []
        for side in SIDES:
            key = _bet_key(symbol, side, bar_open)
            bet = self._open_bets.get(key)
            if not bet or bet.pending:
                continue
            cmds.extend(self._settle(bet, bar_open, open_p, close_p))
        return cmds

    def mark_signaled(self, symbol: str, bar_open: int, side: Side) -> None:
        self._signaled.add(_signal_key(symbol, bar_open, side))

    def is_signaled(self, symbol: str, bar_open: int, side: Side) -> bool:
        return _signal_key(symbol, bar_open, side) in self._signaled

    def unmark_signaled(self, symbol: str, bar_open: int, side: Side) -> None:
        self._signaled.discard(_signal_key(symbol, bar_open, side))

    def attach_open_bet(
        self,
        *,
        bet_id: int,
        binance_symbol: str,
        side: Side,
        candle_open: int,
        cycle_id: int,
        leg: int,
        asset: str,
        poly_series: str,
        entry_price: float,
        shares: float,
        cost_usd: float,
        order_id: str | None,
    ) -> None:
        key = _bet_key(binance_symbol, side, candle_open)
        self._open_bets[key] = _OpenBet(
            bet_id=bet_id,
            cycle_id=cycle_id,
            leg=leg,
            side=side,
            asset=asset,
            binance_symbol=binance_symbol,
            poly_series=poly_series,
            candle_open=candle_open,
            entry_price=entry_price,
            shares=shares,
            cost_usd=cost_usd,
            order_id=order_id,
        )
        ck = _cycle_key(asset, side)
        if leg == 1:
            self._active_cycle[ck] = cycle_id

    def drop_pending(self, binance_symbol: str, side: Side, candle_open: int) -> None:
        key = _bet_key(binance_symbol, side, candle_open)
        self._open_bets.pop(key, None)

    def iter_open_bets(self) -> list[_OpenBet]:
        return list(self._open_bets.values())

    def _maybe_signal(
        self,
        *,
        symbol: str,
        asset: str,
        bar_open: int,
        side: Side,
        total: float,
        signal_ts: int,
    ) -> list[dict]:
        threshold = self._cfg.thresholds.get(asset)
        if threshold is None:
            return []
        sk = _signal_key(symbol, bar_open, side)
        if (
            total < threshold
            or sk in self._signaled
            or _cycle_key(asset, side) in self._active_cycle
        ):
            return []
        target_open = bet_window_open(bar_open)
        return [
            {
                "cmd": "open_bet",
                "mode": self._cfg.mode,
                "side": side,
                "asset": asset,
                "binance_symbol": symbol,
                "leg": 1,
                "candle_open": target_open,
                "signal_time": signal_ts,
                "signal_notional": total,
                "threshold": threshold,
                "liq_bar_open": bar_open,
                "signal_key": sk,
            }
        ]

    def _settle(
        self, bet: _OpenBet, bar_open: int, o: float, c: float
    ) -> list[dict]:
        key = _bet_key(bet.binance_symbol, bet.side, bar_open)
        if key not in self._open_bets:
            return []
        won = _candle_won(bet.side, o, c)
        del self._open_bets[key]
        cmds: list[dict] = [
            {
                "cmd": "settle",
                "mode": self._cfg.mode,
                "side": bet.side,
                "asset": bet.asset,
                "leg": bet.leg,
                "bet_id": bet.bet_id,
                "cycle_id": bet.cycle_id,
                "candle_open": bar_open,
                "won": won,
                "bar_open": o,
                "bar_close": c,
                "entry_price": bet.entry_price,
                "shares": bet.shares,
                "cost_usd": bet.cost_usd,
                "order_id": bet.order_id,
            }
        ]
        ck = _cycle_key(bet.asset, bet.side)
        if bet.leg == 1 and not won and bet.cycle_id is not None:
            next_open = bar_open + WINDOW_SEC
            cmds.append(
                {
                    "cmd": "open_bet",
                    "mode": self._cfg.mode,
                    "side": bet.side,
                    "asset": bet.asset,
                    "binance_symbol": bet.binance_symbol,
                    "leg": 2,
                    "candle_open": next_open,
                    "cycle_id": bet.cycle_id,
                    "signal_time": int(time.time()),
                    "signal_notional": 0.0,
                    "threshold": self._cfg.thresholds.get(bet.asset, 0),
                    "liq_bar_open": bar_open,
                    "signal_key": None,
                }
            )
        else:
            cmds.append(
                {
                    "cmd": "close_cycle",
                    "mode": self._cfg.mode,
                    "cycle_id": bet.cycle_id,
                    "asset": bet.asset,
                    "side": bet.side,
                }
            )
            self._active_cycle.pop(ck, None)
        return cmds
