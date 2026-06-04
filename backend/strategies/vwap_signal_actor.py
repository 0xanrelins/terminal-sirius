"""
VwapSignalActor — 1s INTERNAL bars + native indicators → zone/slope signals.

Components (Nautilus native):
- ``BarType`` ``1-SECOND-LAST-INTERNAL`` aggregated from ``TradeTick``
- ``RollingWindowVwap`` (900-bar rolling VWAP; not daily ``VolumeWeightedAveragePrice``)
- ``LinearRegression`` (900)
- ``AverageTrueRange`` (900)
"""

from __future__ import annotations

from nautilus_trader.common.actor import Actor
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import LinearRegression
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import InstrumentId

from strategies.config import VwapSignalActorConfig
from strategies.indicators.rolling_vwap import RollingWindowVwap
from strategies.messages import VwapZoneSnapshot


class _SymbolIndicators:
    def __init__(self, period: int) -> None:
        self.vwap = RollingWindowVwap(period)
        self.slope = LinearRegression(period)
        self.atr = AverageTrueRange(period)
        self.last_close: float | None = None


class VwapSignalActor(Actor):
    def __init__(self, config: VwapSignalActorConfig) -> None:
        super().__init__(config)
        self._period = int(config.bar_period)
        self._atr_mult = float(config.atr_multiplier)
        self._slope_eps = float(config.slope_range_threshold)
        self._bar_types: dict[str, BarType] = {}
        self._inds: dict[str, _SymbolIndicators] = {}

    def on_start(self) -> None:
        for sym in self.config.instrument_ids:
            iid = InstrumentId.from_str(sym)
            bar_type = BarType.from_str(f"{iid}-1-SECOND-LAST-INTERNAL")
            self._bar_types[sym] = bar_type
            self._inds[sym] = _SymbolIndicators(self._period)
            ind = self._inds[sym]
            self.register_indicator_for_bars(bar_type, ind.vwap)
            self.register_indicator_for_bars(bar_type, ind.slope)
            self.register_indicator_for_bars(bar_type, ind.atr)
            self.subscribe_trade_ticks(iid)
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        symbol = str(bar.bar_type.instrument_id)
        ind = self._inds.get(symbol)
        if ind is None:
            return
        if not (ind.vwap.initialized and ind.slope.initialized and ind.atr.initialized):
            return

        close = bar.close.as_double()
        ind.last_close = close
        vwap = ind.vwap.value
        atr = ind.atr.value
        slope = ind.slope.slope
        low_zone = vwap - atr * self._atr_mult
        high_zone = vwap + atr * self._atr_mult
        ts = bar.ts_event

        self.publish_data(
            DataType(VwapZoneSnapshot),
            VwapZoneSnapshot(
                instrument_id=bar.bar_type.instrument_id,
                vwap=vwap,
                slope=slope,
                low_zone=low_zone,
                high_zone=high_zone,
                close=close,
                ts_event=ts,
                ts_init=ts,
            ),
        )
