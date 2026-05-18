"""
Binance @forceOrder WebSocket — live liquidation feed into data_queue.

Liquidation streams use the /market WebSocket path (not /stream).
See: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Liquidation-Order-Streams
"""
from __future__ import annotations

import asyncio
import json
import queue

import websockets

from liquidations import build_liquidation_message

# All symbols — largest liquidation per symbol per 1s snapshot
WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


async def run_liquidation_stream(
    data_queue: queue.Queue, symbols: tuple[str, ...]
) -> None:
    del symbols  # all-market stream covers every symbol
    subscribed: set[str] = set()

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                print("[liquidations] connected to Binance !forceOrder@arr")
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

                        try:
                            data_queue.put_nowait(msg)
                        except queue.Full:
                            pass

                        sym = msg["symbol"]
                        if sym not in subscribed:
                            subscribed.add(sym)
                            print(
                                f"[liquidations] {sym} {msg['side']} "
                                f"${msg['notional']:,.0f}"
                            )
        except Exception as e:
            print(f"[warn] liquidation stream disconnected: {e}")
            await asyncio.sleep(3)
