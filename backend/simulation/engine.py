"""
Liquidation bar-aggregate → Polymarket UP/DOWN paper simulation engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import db
from adapters.polymarket.gamma import get_token_ids
from adapters.polymarket.rolling import WINDOW_SEC, slug_for_series
from simulation import config
from simulation.config import Side
from simulation.sizing import compute_bet, fetch_min_order_size, pnl_for_outcome


@dataclass
class _OpenBet:
    bet_id: int
    cycle_id: int
    leg: int
    side: Side
    asset: str
    binance_symbol: str
    poly_series: str
    candle_open: int
    entry_price: float
    shares: float
    cost_usd: float


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


class SimulationEngine:
    def __init__(self) -> None:
        self._thresholds = config.thresholds()
        self._min_usd = config.min_usd()
        self._min_shares_default = config.min_shares_default()
        self._bar_long: dict[tuple[str, int], float] = {}
        self._bar_short: dict[tuple[str, int], float] = {}
        self._signaled: set[tuple[str, int, str]] = set()
        self._poly_yes: dict[str, float] = {}
        self._active_cycle: dict[tuple[str, str], int] = {}
        self._open_bets: dict[tuple[str, str, int], _OpenBet] = {}
        self._loaded = False

    async def load_state(self) -> None:
        if self._loaded:
            return
        cycles = await db.get_open_simulation_cycles()
        for c in cycles:
            side = c.get("side") or "long"
            self._active_cycle[_cycle_key(c["asset"], side)] = int(c["id"])
        bets = await db.get_open_bets_for_cycles()
        for b in bets:
            side = b.get("side") or "long"
            key = _bet_key(b["binance_symbol"], side, int(b["candle_open"]))
            self._open_bets[key] = _OpenBet(
                bet_id=int(b["id"]),
                cycle_id=int(b["cycle_id"]),
                leg=int(b["leg"]),
                side=side,
                asset=b["asset"],
                binance_symbol=b["binance_symbol"],
                poly_series=b["poly_series"],
                candle_open=int(b["candle_open"]),
                entry_price=float(b["entry_price"]),
                shares=float(b["shares"]),
                cost_usd=float(b["cost_usd"]),
            )
        self._loaded = True
        print(f"[simulation] restored {len(cycles)} cycle(s), {len(bets)} open bet(s)")

    async def on_message(self, msg: dict) -> list[dict]:
        if not config.is_enabled():
            return []
        await self.load_state()
        mtype = msg.get("type")
        if mtype == "liquidation":
            return await self._on_liquidation(msg)
        if mtype == "bar" and msg.get("interval") == "15m":
            return await self._on_bar(msg)
        if mtype == "polymarket":
            return self._on_polymarket(msg)
        return []

    def _on_polymarket(self, msg: dict) -> list[dict]:
        series = msg.get("series")
        if not series or series not in config.SERIES_TO_ASSET:
            return []
        price = float(msg.get("yes_price") or 0)
        if price > 0:
            self._poly_yes[series] = price
        return []

    async def _on_liquidation(self, msg: dict) -> list[dict]:
        symbol = msg.get("symbol")
        if not symbol or symbol not in config.BINANCE_TO_ASSET:
            return []

        asset = config.BINANCE_TO_ASSET[symbol]
        updates = msg.get("_updates") or []
        events: list[dict] = []
        signal_ts = int(msg.get("time") or time.time())
        threshold = self._thresholds.get(asset, config.DEFAULT_THRESHOLDS[asset])
        target_open = config.next_window_open(signal_ts)

        for u in updates:
            if u.get("interval") != "15m":
                continue
            bar_open = int(u["time"])
            long_delta = float(u.get("long_delta") or 0)
            short_delta = float(u.get("short_delta") or 0)

            if long_delta > 0:
                lk = (symbol, bar_open)
                long_total = self._bar_long.get(lk, 0.0) + long_delta
                self._bar_long[lk] = long_total
                sk = _signal_key(symbol, bar_open, "long")
                if (
                    long_total >= threshold
                    and sk not in self._signaled
                    and _cycle_key(asset, "long") not in self._active_cycle
                ):
                    self._signaled.add(sk)
                    evs = await self._open_bet(
                        side="long",
                        asset=asset,
                        binance_symbol=symbol,
                        leg=1,
                        candle_open=target_open,
                        signal_time=signal_ts,
                        signal_notional=long_total,
                        threshold=threshold,
                    )
                    events.extend(evs)

            if short_delta > 0:
                sk_bar = (symbol, bar_open)
                short_total = self._bar_short.get(sk_bar, 0.0) + short_delta
                self._bar_short[sk_bar] = short_total
                sig_k = _signal_key(symbol, bar_open, "short")
                if (
                    short_total >= threshold
                    and sig_k not in self._signaled
                    and _cycle_key(asset, "short") not in self._active_cycle
                ):
                    self._signaled.add(sig_k)
                    evs = await self._open_bet(
                        side="short",
                        asset=asset,
                        binance_symbol=symbol,
                        leg=1,
                        candle_open=target_open,
                        signal_time=signal_ts,
                        signal_notional=short_total,
                        threshold=threshold,
                    )
                    events.extend(evs)

        return events

    async def _on_bar(self, msg: dict) -> list[dict]:
        symbol = msg.get("symbol")
        if not symbol or symbol not in config.BINANCE_TO_ASSET:
            return []

        bar_open = int(msg.get("time") or 0)
        o, c = float(msg["open"]), float(msg["close"])
        events: list[dict] = []

        for side in config.SIDES:
            key = _bet_key(symbol, side, bar_open)
            bet = self._open_bets.get(key)
            if not bet:
                continue

            won = _candle_won(side, o, c)
            settled_at = int(time.time())
            outcome = "win" if won else "loss"
            pnl = pnl_for_outcome(bet.shares, bet.cost_usd, won)
            await db.settle_simulation_bet(bet.bet_id, outcome, pnl, settled_at)
            del self._open_bets[key]

            events.append(
                self._evt_settle(bet, outcome, pnl, won, o, c, settled_at)
            )

            ck = _cycle_key(bet.asset, bet.side)
            if bet.leg == 1 and not won:
                next_open = bar_open + WINDOW_SEC
                evs = await self._open_bet(
                    side=bet.side,
                    asset=bet.asset,
                    binance_symbol=bet.binance_symbol,
                    leg=2,
                    candle_open=next_open,
                    cycle_id=bet.cycle_id,
                )
                events.extend(evs)
                if not evs:
                    await db.close_simulation_cycle(bet.cycle_id)
                    self._active_cycle.pop(ck, None)
            else:
                await db.close_simulation_cycle(bet.cycle_id)
                self._active_cycle.pop(ck, None)
                events.append({
                    "type": "simulation_cycle_closed",
                    "cycle_id": bet.cycle_id,
                    "asset": bet.asset,
                    "side": bet.side,
                })

        return events

    async def _open_bet(
        self,
        *,
        side: Side,
        asset: str,
        binance_symbol: str,
        leg: int,
        candle_open: int,
        signal_time: int | None = None,
        signal_notional: float | None = None,
        threshold: float | None = None,
        cycle_id: int | None = None,
    ) -> list[dict]:
        meta = config.ASSETS[asset]
        series = meta["poly_series"]
        yes_price = self._poly_yes.get(series)
        if not yes_price or yes_price <= 0:
            print(f"[simulation] skip {asset} {side} leg{leg}: no poly price")
            return []

        if side == "long":
            entry_price = yes_price
        else:
            entry_price = round(1.0 - yes_price, 4)
            if entry_price <= 0 or entry_price >= 1:
                print(f"[simulation] skip {asset} short leg{leg}: invalid NO price")
                return []

        slug = slug_for_series(series, ts=candle_open)
        opened_at = int(time.time())

        min_shares = self._min_shares_default
        try:
            info = await get_token_ids(slug)
            if info:
                token = info.get("yes") if side == "long" else info.get("no")
                if token:
                    min_shares = await fetch_min_order_size(
                        token, self._min_shares_default
                    )
        except Exception:
            pass

        try:
            shares, cost_usd = compute_bet(entry_price, min_shares, self._min_usd)
        except ValueError as e:
            print(f"[simulation] sizing error {asset} {side}: {e}")
            return []

        ck = _cycle_key(asset, side)
        if cycle_id is None:
            cycle_id = await db.create_simulation_cycle(
                asset=asset,
                binance_symbol=binance_symbol,
                poly_series=series,
                signal_time=signal_time or opened_at,
                side=side,
                signal_notional=signal_notional or 0,
                threshold=threshold or self._thresholds.get(asset, 0),
            )
            self._active_cycle[ck] = cycle_id

        bet_id = await db.insert_simulation_bet(
            cycle_id=cycle_id,
            leg=leg,
            side=side,
            candle_open=candle_open,
            poly_slug=slug,
            poly_series=series,
            entry_price=entry_price,
            shares=shares,
            cost_usd=cost_usd,
            opened_at=opened_at,
        )

        bkey = _bet_key(binance_symbol, side, candle_open)
        self._open_bets[bkey] = _OpenBet(
            bet_id=bet_id,
            cycle_id=cycle_id,
            leg=leg,
            side=side,
            asset=asset,
            binance_symbol=binance_symbol,
            poly_series=series,
            candle_open=candle_open,
            entry_price=entry_price,
            shares=shares,
            cost_usd=cost_usd,
        )

        direction = "UP" if side == "long" else "DN"
        events: list[dict] = []
        if leg == 1:
            sig_evt: dict = {
                "type": "simulation_signal",
                "side": side,
                "asset": asset,
                "cycle_id": cycle_id,
                "binance_symbol": binance_symbol,
                "poly_series": series,
                "signal_time": signal_time,
                "threshold": threshold,
                "target_candle_open": candle_open,
            }
            if side == "long":
                sig_evt["signal_long_notional"] = signal_notional
            else:
                sig_evt["signal_short_notional"] = signal_notional
            events.append(sig_evt)

        events.append({
            "type": "simulation_bet_open",
            "bet_id": bet_id,
            "cycle_id": cycle_id,
            "side": side,
            "asset": asset,
            "leg": leg,
            "binance_symbol": binance_symbol,
            "poly_series": series,
            "poly_slug": slug,
            "candle_open": candle_open,
            "entry_price": entry_price,
            "shares": shares,
            "cost_usd": cost_usd,
            "opened_at": opened_at,
        })
        print(
            f"[simulation] {asset} {side} leg{leg} {direction} @ {entry_price:.3f} "
            f"{shares:.0f} sh ${cost_usd:.2f} → candle {candle_open}"
        )
        return events

    @staticmethod
    def _evt_settle(
        bet: _OpenBet,
        outcome: str,
        pnl: float,
        won: bool,
        bar_open: float,
        bar_close: float,
        settled_at: int,
    ) -> dict:
        green = bar_close >= bar_open
        return {
            "type": "simulation_bet_settle",
            "bet_id": bet.bet_id,
            "cycle_id": bet.cycle_id,
            "side": bet.side,
            "asset": bet.asset,
            "leg": bet.leg,
            "candle_open": bet.candle_open,
            "outcome": outcome,
            "pnl_usd": pnl,
            "won": won,
            "bar_open": bar_open,
            "bar_close": bar_close,
            "candle_green": green,
            "settled_at": settled_at,
        }
