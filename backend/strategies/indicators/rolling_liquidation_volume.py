"""
Rolling liquidation notional (USD) over a time window in seconds.

No native Nautilus liquidation-volume indicator; this follows the same
``Indicator`` extension pattern as ``RollingWindowVwap``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick


@dataclass(frozen=True)
class _LiqEvent:
    ts_event: int
    signed_notional: float  # positive = long liq (SELL), negative = short liq (BUY)


class RollingLiquidationVolume(Indicator):
    """
    Sum of liquidation notional in a rolling ``window_sec`` window.

    Binance force-order: SELL → long liquidation, BUY → short liquidation.
    """

    def __init__(self, window_sec: int) -> None:
        PyCondition.positive_int(window_sec, "window_sec")
        super().__init__(params=[window_sec])
        self.window_sec = window_sec
        self._window_ns = window_sec * 1_000_000_000
        self._events: deque[_LiqEvent] = deque()
        self.long_volume = 0.0
        self.short_volume = 0.0

    def handle_bar(self, bar: Bar) -> None:
        raise NotImplementedError("RollingLiquidationVolume is event-driven")

    def handle_quote_tick(self, tick: QuoteTick) -> None:
        raise NotImplementedError("RollingLiquidationVolume is event-driven")

    def handle_trade_tick(self, tick: TradeTick) -> None:
        raise NotImplementedError("RollingLiquidationVolume is event-driven")

    def update_long_liquidation(self, *, ts_event: int, notional: float) -> None:
        self._append(ts_event, notional, is_long=True)

    def update_short_liquidation(self, *, ts_event: int, notional: float) -> None:
        self._append(ts_event, notional, is_long=False)

    def _append(self, ts_event: int, notional: float, *, is_long: bool) -> None:
        if notional <= 0:
            return
        signed = notional if is_long else -notional
        self._events.append(_LiqEvent(ts_event=ts_event, signed_notional=signed))
        self._recompute(ts_event)

    def _recompute(self, now_ns: int) -> None:
        cutoff = now_ns - self._window_ns
        while self._events and self._events[0].ts_event < cutoff:
            self._events.popleft()
        long_v = 0.0
        short_v = 0.0
        for ev in self._events:
            if ev.signed_notional > 0:
                long_v += ev.signed_notional
            else:
                short_v += abs(ev.signed_notional)
        self.long_volume = long_v
        self.short_volume = short_v
        self._set_has_inputs(True)
        self._set_initialized(True)

    def _reset(self) -> None:
        self._events.clear()
        self.long_volume = 0.0
        self.short_volume = 0.0
