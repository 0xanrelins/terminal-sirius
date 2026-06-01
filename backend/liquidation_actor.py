"""
LiquidationActor — Binance !forceOrder@arr inside Nautilus node lifecycle.

Same messages as liquidation_stream (build_liquidation_message → data_queue).
"""
from __future__ import annotations

import asyncio
import json
import queue
from typing import Optional

import websockets
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import DataType

from liquidations import build_liquidation_message
from recorders.data_types import BinanceLiquidationEvent

WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


class LiquidationActorConfig(ActorConfig, frozen=True):
    pass


class LiquidationActor(Actor):
    def __init__(self, config: LiquidationActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._stream_task: Optional[asyncio.Task] = None

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def on_start(self) -> None:
        self._stream_task = asyncio.create_task(self._run_stream())

    def on_stop(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def on_dispose(self) -> None:
        self.on_stop()

    async def _run_stream(self) -> None:
        subscribed: set[str] = set()
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    print("[liquidations] LiquidationActor connected to !forceOrder@arr")
                    async for raw in ws:
                        envelope = json.loads(raw)
                        data = envelope.get("data", envelope)
                        events = data if isinstance(data, list) else [data]
                        for item in events:
                            if item.get("e") != "forceOrder":
                                continue
                            msg = build_liquidation_message(item)
                            if msg is None:
                                continue
                            self._enqueue(msg)
                            order = (item.get("o") or {})
                            try:
                                side_raw = str(order.get("S", ""))
                                liq_side = "LONG" if side_raw == "SELL" else "SHORT"
                                price = float(order.get("ap") or 0.0)
                                quantity = float(order.get("z") or 0.0)
                                ts_event = int(order.get("T") or 0) * 1_000_000
                                self.publish_data(
                                    DataType(BinanceLiquidationEvent),
                                    BinanceLiquidationEvent(
                                        ts_event=ts_event,
                                        ts_init=ts_event,
                                        symbol=str(msg["symbol"]),
                                        side=liq_side,
                                        price=price,
                                        quantity=quantity,
                                    ),
                                )
                            except Exception:
                                pass
                            sym = msg["symbol"]
                            if sym not in subscribed:
                                subscribed.add(sym)
                                print(
                                    f"[liquidations] {sym} {msg['side']} "
                                    f"${msg['notional']:,.0f}"
                                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[warn] LiquidationActor disconnected: {e}")
                await asyncio.sleep(3)
