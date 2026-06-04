"""
Custom Binance liquidation feed → serializable ``LiquidationTick`` on the msgbus.

Native ``BinanceFuturesLiquidation`` is unusable in Nautilus 1.228.0: the pyo3 (Rust)
liquidation object cannot be delivered through the Cython ``_handle_data`` pipeline
(``TypeError``). Until that is fixed upstream, this actor ingests the raw all-market
``!forceOrder@arr`` stream and republishes each event as a ``LiquidationTick``
(``@customdataclass`` → serializable), so both ``LiquidationSignalActor`` and
``LiquidationUiBridgeActor`` consume it via ``subscribe_data`` and it persists to the
catalog (``StreamingConfig``) for backtests.

Live-only (own WS connection); backtests replay ``LiquidationTick`` from the catalog.
"""
from __future__ import annotations

import asyncio
import json

import websockets

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import DataType

from liquidations import binance_to_nautilus
from recorders.data_types import LiquidationTick

WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


class LiquidationFeedActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...] = ()
    ws_url: str = WS_URL


class LiquidationFeedActor(Actor):
    """Publish ``LiquidationTick`` from Binance ``!forceOrder@arr`` (filtered to config symbols)."""

    def __init__(self, config: LiquidationFeedActorConfig) -> None:
        super().__init__(config)
        self._symbols: set[str] = set(config.instrument_ids)
        self._task: asyncio.Task | None = None

    def on_start(self) -> None:
        self._task = asyncio.create_task(self._run_ws())

    def on_stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run_ws(self) -> None:
        while True:
            try:
                async with websockets.connect(self.config.ws_url, ping_interval=20) as ws:
                    self.log.info("Liquidation feed connected (!forceOrder@arr)")
                    async for raw in ws:
                        self._handle_raw(raw)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 — reconnect on any WS error
                self.log.warning(f"Liquidation feed disconnected: {e!r}")
                await asyncio.sleep(3)

    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        data = envelope.get("data", envelope)
        events = data if isinstance(data, list) else [data]
        for item in events:
            if not isinstance(item, dict) or item.get("e") != "forceOrder":
                continue
            order = item.get("o") or {}
            try:
                symbol = binance_to_nautilus(str(order["s"]))
                if self._symbols and symbol not in self._symbols:
                    continue
                side = str(order["S"])
                price = float(order["ap"])
                quantity = float(order["z"])
                ts = int(order["T"]) * 1_000_000  # ms → ns
            except (KeyError, TypeError, ValueError):
                continue
            self.publish_data(
                DataType(LiquidationTick),
                LiquidationTick(
                    symbol=symbol,
                    side=side,
                    notional=round(price * quantity, 2),
                    price=price,
                    quantity=quantity,
                    ts_event=ts,
                    ts_init=ts,
                ),
            )
