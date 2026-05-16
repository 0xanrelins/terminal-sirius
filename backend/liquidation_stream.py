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

from liquidations import binance_to_nautilus, record_liquidation

# All symbols — largest liquidation per symbol per 1s snapshot
WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


async def run_liquidation_stream(
    data_queue: queue.Queue, symbols: tuple[str, ...]
) -> None:
    del symbols  # all-market stream covers every symbol
    subscribed = set()

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                print("[liquidations] connected to Binance !forceOrder@arr")
                async for raw in ws:
                    envelope = json.loads(raw)
                    data = envelope.get("data", envelope)

                    # All-market stream wraps events in an array
                    events = data if isinstance(data, list) else [data]
                    for item in events:
                        if item.get("e") != "forceOrder":
                            continue

                        order = item["o"]
                        symbol = binance_to_nautilus(order["s"])
                        side = order["S"]
                        notional = float(order["ap"]) * float(order["z"])
                        trade_ms = int(order["T"])

                        updates = record_liquidation(symbol, side, notional, trade_ms)
                        try:
                            data_queue.put_nowait({
                                "type": "liquidation",
                                "symbol": symbol,
                                "side": side,
                                "notional": round(notional, 2),
                                "time": trade_ms // 1000,
                                "_updates": updates,
                            })
                        except queue.Full:
                            pass

                        if symbol not in subscribed:
                            subscribed.add(symbol)
                            print(f"[liquidations] {symbol} {side} ${notional:,.0f}")
        except Exception as e:
            print(f"[warn] liquidation stream disconnected: {e}")
            await asyncio.sleep(3)
