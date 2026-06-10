"""
LiquidationSignalActor — single-event notional gate → ``publish_data`` LiquidationTrigger.

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
        self._thresholds = {sym: threshold_for_symbol(config, sym) for sym in self._symbols}

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
        if symbol not in self._thresholds:
            return
        threshold = self._thresholds[symbol]
        if notional < threshold:
            return
        long_hit = side == "SELL"
        short_hit = side == "BUY"
        if not long_hit and not short_hit:
            return
        self.publish_data(
            DataType(LiquidationVolumeSnapshot),
            LiquidationVolumeSnapshot(
                instrument_id=InstrumentId.from_str(symbol),
                long_volume=notional if long_hit else 0.0,
                short_volume=notional if short_hit else 0.0,
                long_hit=long_hit,
                short_hit=short_hit,
                ts_event=ts_event,
                ts_init=ts_event,
            ),
        )
        self.publish_data(
            DataType(LiquidationTrigger),
            LiquidationTrigger(
                instrument_id=InstrumentId.from_str(symbol),
                long_triggered=long_hit,
                short_triggered=short_hit,
                ts_event=ts_event,
                ts_init=ts_event,
            ),
        )
