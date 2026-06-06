"""
LiquidationSignalActor — rolling 900s liq volume → ``publish_data`` LiquidationTrigger.

Data: ``LiquidationTick`` from ``LiquidationFeedActor`` (custom Binance ``!forceOrder``
feed). Native ``BinanceFuturesLiquidation`` is unusable in Nautilus 1.228.0 (pyo3 type
cannot enter the Cython data pipeline).
"""

from __future__ import annotations

from nautilus_trader.common.actor import Actor
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import InstrumentId

from recorders.data_types import LiquidationTick
from strategies.config import LiquidationSignalActorConfig
from strategies.subscriptions import subscribe_custom_data
from strategies.indicators.rolling_liquidation_volume import RollingLiquidationVolume
from strategies.messages import LiquidationTrigger
from strategies.messages import LiquidationVolumeSnapshot


def threshold_for_symbol(config: LiquidationSignalActorConfig, symbol: str) -> float:
    if "BTC" in symbol:
        return float(config.liq_threshold_btc)
    if "ETH" in symbol:
        return float(config.liq_threshold_eth)
    if "SOL" in symbol:
        return float(config.liq_threshold_sol)
    if "XRP" in symbol:
        return float(config.liq_threshold_xrp)
    if "DOGE" in symbol:
        return float(config.liq_threshold_doge)
    return float(config.liq_threshold_doge)


class LiquidationSignalActor(Actor):
    def __init__(self, config: LiquidationSignalActorConfig) -> None:
        super().__init__(config)
        self._symbols = tuple(config.instrument_ids)
        self._indicators: dict[str, RollingLiquidationVolume] = {
            sym: RollingLiquidationVolume(int(config.window_sec)) for sym in self._symbols
        }
        self._thresholds = {sym: threshold_for_symbol(config, sym) for sym in self._symbols}
        self._last_long_trigger: dict[str, bool] = {sym: False for sym in self._symbols}
        self._last_short_trigger: dict[str, bool] = {sym: False for sym in self._symbols}

    def on_start(self) -> None:
        subscribe_custom_data(
            self,
            LiquidationTick,
            backtest=self.config.backtest_mode,
        )

    def on_data(self, data: Data) -> None:
        if not isinstance(data, LiquidationTick):
            return
        symbol = data.symbol
        side = data.side
        notional = data.notional
        ts_event = int(data.ts_event)
        if symbol not in self._indicators:
            return
        ind = self._indicators[symbol]
        if side == "SELL":
            ind.update_long_liquidation(ts_event=ts_event, notional=notional)
        elif side == "BUY":
            ind.update_short_liquidation(ts_event=ts_event, notional=notional)
        else:
            return
        self._maybe_publish_triggers(symbol, ind, ts_event)

    def _maybe_publish_triggers(
        self,
        symbol: str,
        ind: RollingLiquidationVolume,
        ts_event: int,
    ) -> None:
        if not ind.initialized:
            return
        threshold = self._thresholds[symbol]
        long_hit = ind.long_volume >= threshold
        short_hit = ind.short_volume >= threshold
        self.publish_data(
            DataType(LiquidationVolumeSnapshot),
            LiquidationVolumeSnapshot(
                instrument_id=InstrumentId.from_str(symbol),
                long_volume=ind.long_volume,
                short_volume=ind.short_volume,
                long_hit=long_hit,
                short_hit=short_hit,
                ts_event=ts_event,
                ts_init=ts_event,
            ),
        )
        long_edge = long_hit and not self._last_long_trigger[symbol]
        short_edge = short_hit and not self._last_short_trigger[symbol]
        if long_edge or short_edge:
            self.publish_data(
                DataType(LiquidationTrigger),
                LiquidationTrigger(
                    instrument_id=InstrumentId.from_str(symbol),
                    long_triggered=long_edge,
                    short_triggered=short_edge,
                    ts_event=ts_event,
                    ts_init=ts_event,
                ),
            )
        self._last_long_trigger[symbol] = long_hit
        self._last_short_trigger[symbol] = short_hit
