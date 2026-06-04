"""
Rolling VWAP over the last N bars — Nautilus ``Indicator`` subclass.

Native ``VolumeWeightedAveragePrice`` resets daily; period-900 VWAP follows the
official example pattern in ``nautilus_trader/examples/live/binance/rolling_vwap_sol.py``.
"""

from __future__ import annotations

from collections import deque

from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick


class RollingWindowVwap(Indicator):
    """Typical-price VWAP over a rolling window of ``period`` bars."""

    def __init__(self, period: int) -> None:
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period])
        self.period = period
        self._rows: deque[tuple[float, float]] = deque(maxlen=period)
        self._tpv_sum = 0.0
        self._vol_sum = 0.0
        self.value = 0.0

    def handle_quote_tick(self, tick: QuoteTick) -> None:
        PyCondition.not_none(tick, "tick")
        raise NotImplementedError("RollingWindowVwap is bar-only")

    def handle_trade_tick(self, tick: TradeTick) -> None:
        PyCondition.not_none(tick, "tick")
        raise NotImplementedError("RollingWindowVwap is bar-only")

    def handle_bar(self, bar: Bar) -> None:
        PyCondition.not_none(bar, "bar")
        tp = (
            bar.high.as_double() + bar.low.as_double() + bar.close.as_double()
        ) / 3.0
        vol = bar.volume.as_double()
        tpv = tp * vol if vol != 0.0 else 0.0

        if len(self._rows) == self._rows.maxlen:
            old_tpv, old_v = self._rows.popleft()
            self._tpv_sum -= old_tpv
            self._vol_sum -= old_v

        self._rows.append((tpv, vol))
        self._tpv_sum += tpv
        self._vol_sum += vol
        self.value = self._tpv_sum / self._vol_sum if self._vol_sum > 0.0 else tp

        self._set_has_inputs(True)
        if len(self._rows) >= self.period:
            self._set_initialized(True)

    def _reset(self) -> None:
        self._rows.clear()
        self._tpv_sum = 0.0
        self._vol_sum = 0.0
        self.value = 0.0
