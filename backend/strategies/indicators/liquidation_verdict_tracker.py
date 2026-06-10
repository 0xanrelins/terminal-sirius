"""
Per-symbol open liquidation verdict trackers.

Follows the same custom ``Indicator`` extension pattern as ``RollingLiquidationVolume``.
"""

from __future__ import annotations

from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick

from strategies.liquidation_verdict_logic import CompletedVerdict
from strategies.liquidation_verdict_logic import OpenVerdictEvent
from strategies.liquidation_verdict_logic import expire_open_event
from strategies.liquidation_verdict_logic import update_open_event


class LiquidationVerdictTracker(Indicator):
    """Track causal post-liquidation paths for single-print events."""

    def __init__(
        self,
        *,
        max_observation_sec: int,
        liq_move_threshold_pct: float,
        recovery_move_threshold_pct: float,
    ) -> None:
        PyCondition.positive_int(max_observation_sec, "max_observation_sec")
        PyCondition.positive(liq_move_threshold_pct, "liq_move_threshold_pct")
        PyCondition.positive(recovery_move_threshold_pct, "recovery_move_threshold_pct")
        super().__init__(
            params=[
                max_observation_sec,
                liq_move_threshold_pct,
                recovery_move_threshold_pct,
            ]
        )
        self.max_observation_sec = max_observation_sec
        self.liq_move_threshold_pct = liq_move_threshold_pct
        self.recovery_move_threshold_pct = recovery_move_threshold_pct
        self._window_ns = max_observation_sec * 1_000_000_000
        self._open: dict[str, OpenVerdictEvent] = {}

    def handle_bar(self, bar: Bar) -> None:
        raise NotImplementedError("LiquidationVerdictTracker is event-driven")

    def handle_quote_tick(self, tick: QuoteTick) -> None:
        raise NotImplementedError("LiquidationVerdictTracker is event-driven")

    def handle_trade_tick(self, tick: TradeTick) -> None:
        raise NotImplementedError("LiquidationVerdictTracker is event-driven")

    @property
    def open_count(self) -> int:
        return len(self._open)

    def open_event(self, event: OpenVerdictEvent) -> None:
        self._open[event.event_id] = event
        self._set_has_inputs(True)
        self._set_initialized(True)

    def update_price(
        self,
        *,
        price: float,
        ts_ns: int,
    ) -> list[CompletedVerdict]:
        completed: list[CompletedVerdict] = []
        expired_ids: list[str] = []
        for event_id, event in list(self._open.items()):
            verdict = update_open_event(
                event,
                price,
                ts_ns,
                liq_move_threshold_pct=self.liq_move_threshold_pct,
                recovery_move_threshold_pct=self.recovery_move_threshold_pct,
            )
            if verdict is not None:
                completed.append(verdict)
                expired_ids.append(event_id)
                continue
            if ts_ns - event.event_ts_ns >= self._window_ns:
                completed.append(expire_open_event(event))
                expired_ids.append(event_id)
        for event_id in expired_ids:
            self._open.pop(event_id, None)
        return completed

    def _reset(self) -> None:
        self._open.clear()
