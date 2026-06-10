"""
LiquidationVerdictActor — single-print liquidation path → ``LiquidationVerdict``.

Consumes ``LiquidationTick`` + ``TradeTick`` (native Nautilus data). Publishes a
verdict when a move threshold is hit or the observation window expires.
"""

from __future__ import annotations

import json

from nautilus_trader.common.actor import Actor
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId

from recorders.data_types import LiquidationTick
from strategies.config import LiquidationVerdictActorConfig
from strategies.indicators.liquidation_verdict_tracker import LiquidationVerdictTracker
from strategies.liquidation_verdict_logic import CompletedVerdict
from strategies.liquidation_verdict_logic import OpenVerdictEvent
from strategies.liquidation_verdict_logic import VerdictEventIdFactory
from strategies.messages import LiquidationVerdict
from strategies.messages import LiquidationVerdictStatus
from strategies.subscriptions import subscribe_custom_data


def min_notional_for_symbol(config: LiquidationVerdictActorConfig, symbol: str) -> float:
    if config.min_notional > 0:
        return float(config.min_notional)
    if "BTC" in symbol:
        return float(config.min_notional_btc)
    if "ETH" in symbol:
        return float(config.min_notional_eth)
    if "SOL" in symbol:
        return float(config.min_notional_sol)
    if "XRP" in symbol:
        return float(config.min_notional_xrp)
    if "DOGE" in symbol:
        return float(config.min_notional_doge)
    return float(config.min_notional_doge)


def _normalize_liq_side(side: str) -> str | None:
    s = side.strip().upper()
    if s in ("LONG", "SHORT"):
        return s
    if s == "SELL":
        return "LONG"
    if s == "BUY":
        return "SHORT"
    return None


class LiquidationVerdictActor(Actor):
    def __init__(self, config: LiquidationVerdictActorConfig) -> None:
        super().__init__(config)
        self._symbols = tuple(config.instrument_ids)
        self._last_price: dict[str, float] = {}
        self._trackers: dict[str, LiquidationVerdictTracker] = {
            sym: LiquidationVerdictTracker(
                max_observation_sec=int(config.max_observation_sec),
                liq_move_threshold_pct=float(config.liq_move_threshold_pct),
                recovery_move_threshold_pct=float(config.recovery_move_threshold_pct),
            )
            for sym in self._symbols
        }
        self._min_notional = {
            sym: min_notional_for_symbol(config, sym) for sym in self._symbols
        }
        self._event_ids = VerdictEventIdFactory()

    def on_start(self) -> None:
        subscribe_custom_data(
            self,
            LiquidationTick,
            backtest=self.config.backtest_mode,
        )
        for sym in self._symbols:
            self.subscribe_trade_ticks(InstrumentId.from_str(sym))

    def on_data(self, data: Data) -> None:
        if isinstance(data, LiquidationTick):
            self._on_liquidation_tick(data)

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._on_trade_tick(tick)

    def _on_liquidation_tick(self, tick: LiquidationTick) -> None:
        symbol = tick.symbol
        if symbol not in self._trackers:
            return
        liq_side = _normalize_liq_side(str(tick.side))
        if liq_side is None:
            return
        notional = float(tick.notional) if tick.notional else float(tick.price) * float(
            tick.quantity
        )
        if notional < self._min_notional[symbol]:
            return
        ts_ns = int(tick.ts_event)
        anchor = self._last_price.get(symbol, float(tick.price))
        if anchor <= 0:
            anchor = float(tick.price)
        order_id = int(getattr(tick, "order_id", 0) or 0)
        event = OpenVerdictEvent(
            event_id=self._event_ids.make(
                symbol,
                liq_side,
                ts_ns,
                order_id=order_id,
            ),
            symbol=symbol,
            liq_side=liq_side,  # type: ignore[arg-type]
            notional=notional,
            event_price=anchor,
            event_ts_ns=ts_ns,
        )
        self._trackers[symbol].open_event(event)
        self._publish_completed(
            self._trackers[symbol].update_price(price=anchor, ts_ns=ts_ns)
        )
        self._publish_pending_status()

    def _on_trade_tick(self, tick: TradeTick) -> None:
        symbol = str(tick.instrument_id)
        if symbol not in self._trackers:
            return
        price = float(tick.price.as_double())
        ts_ns = int(tick.ts_event)
        self._last_price[symbol] = price
        self._publish_completed(
            self._trackers[symbol].update_price(price=price, ts_ns=ts_ns)
        )

    def _pending_snapshot(self) -> tuple[int, dict[str, int]]:
        total = 0
        by_coin: dict[str, int] = {}
        for sym, tracker in self._trackers.items():
            count = tracker.open_count
            if count <= 0:
                continue
            coin = sym.split("USDT")[0]
            by_coin[coin] = count
            total += count
        return total, by_coin

    def _now_ns(self) -> int:
        clock = getattr(self, "clock", None)
        if clock is not None:
            return int(clock.timestamp_ns())
        import time

        return time.time_ns()

    def _publish_pending_status(self) -> None:
        total, by_coin = self._pending_snapshot()
        now = self._now_ns()
        self.publish_data(
            DataType(LiquidationVerdictStatus),
            LiquidationVerdictStatus(
                pending_total=total,
                pending_by_coin_json=json.dumps(by_coin, sort_keys=True),
                ts_event=now,
                ts_init=now,
            ),
        )

    def _publish_completed(self, completed: list[CompletedVerdict]) -> None:
        for verdict in completed:
            self.publish_data(
                DataType(LiquidationVerdict),
                LiquidationVerdict(
                    instrument_id=InstrumentId.from_str(verdict.symbol),
                    event_id=verdict.event_id,
                    liq_side=verdict.liq_side,
                    notional=verdict.notional,
                    event_price=verdict.event_price,
                    winner=verdict.winner,
                    liq_move_pct=verdict.liq_move_pct,
                    recovery_move_pct=verdict.recovery_move_pct,
                    dominance_ratio=verdict.dominance_ratio,
                    time_to_dominance_sec=verdict.time_to_dominance_sec,
                    area_bias=verdict.area_bias,
                    status=verdict.status,
                    completion_reason=verdict.completion_reason,
                    ts_event=verdict.event_ts_ns,
                    ts_init=verdict.event_ts_ns
                    + int(verdict.time_to_dominance_sec * 1_000_000_000),
                ),
            )
        if completed:
            self._publish_pending_status()
