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
from strategies.liq_poly_data import LiqBar15mUpdate

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
                            for u in msg.get("_updates") or []:
                                if u.get("interval") != "15m":
                                    continue
                                bar_open = int(u["time"])
                                long_t = short_t = 0.0
                                for b in msg.get("bars") or []:
                                    if b.get("interval") == "15m" and int(b["time"]) == bar_open:
                                        long_t = float(b["long"])
                                        short_t = float(b["short"])
                                        break
                                ts_event = bar_open * 1_000_000_000
                                update = LiqBar15mUpdate(
                                    ts_event=ts_event,
                                    ts_init=ts_event,
                                    symbol=str(msg["symbol"]),
                                    bar_open=bar_open,
                                    long_total=long_t,
                                    short_total=short_t,
                                    signal_ts=int(msg.get("time") or 0),
                                )
                                self.publish_data(DataType(LiqBar15mUpdate), update)
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
