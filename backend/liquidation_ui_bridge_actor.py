"""Forward native ``BinanceFuturesLiquidation`` events to the FastAPI WS queue."""
from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from nautilus_trader.adapters.binance import BinanceFuturesLiquidation
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId

from liquidations import liquidation_message_from_native

if TYPE_CHECKING:
    import multiprocessing


class LiquidationUiBridgeActorConfig(ActorConfig, frozen=True):
    pass


class LiquidationUiBridgeActor(Actor):
    """Subscribe to native liquidations and enqueue UI/DB messages (no extra WS)."""

    def __init__(
        self,
        config: LiquidationUiBridgeActorConfig,
        data_queue: queue.Queue | multiprocessing.queues.Queue,
    ) -> None:
        super().__init__(config)
        self._queue = data_queue

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def on_start(self) -> None:
        self.subscribe_data(
            DataType(BinanceFuturesLiquidation),
            client_id=ClientId("BINANCE"),
        )
        print("[liquidations] LiquidationUiBridge → native BinanceFuturesLiquidation → WS queue")

    def on_data(self, data: Data) -> None:
        if not isinstance(data, BinanceFuturesLiquidation):
            return
        msg = liquidation_message_from_native(data)
        if msg is not None:
            self._enqueue(msg)
