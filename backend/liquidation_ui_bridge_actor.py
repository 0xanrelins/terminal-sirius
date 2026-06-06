"""Forward native ``BinanceFuturesLiquidation`` events to the FastAPI WS queue."""
from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType

from liquidations import liquidation_message_from_tick
from recorders.data_types import LiquidationTick

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
        # Live custom data from LiquidationFeedActor arrives on msgbus topic `data.<DataType.topic>`.
        self.msgbus.subscribe(
            topic=f"data.{DataType(LiquidationTick).topic}",
            handler=self.handle_data,
        )
        print("[liquidations] LiquidationUiBridge → LiquidationTick → WS queue")

    def on_data(self, data: Data) -> None:
        if not isinstance(data, LiquidationTick):
            return
        msg = liquidation_message_from_tick(data)
        if msg is not None:
            self._enqueue(msg)
