"""
Custom Binance liquidation feed → ``LiquidationTick`` on the msgbus + Parquet catalog.

Native ``BinanceFuturesLiquidation`` is unusable in Nautilus 1.228.0: the pyo3 (Rust)
liquidation object cannot be delivered through the Cython ``_handle_data`` pipeline
(``TypeError``). Until that is fixed upstream, this actor ingests the raw all-market
``!forceOrder@arr`` stream and republishes each event as a ``LiquidationTick``
(``@customdataclass`` → serializable) for ``LiquidationSignalActor`` and
``LiquidationUiBridgeActor`` via ``subscribe_data``.

``StreamingConfig`` does not capture ``Actor.publish_data``; when ``catalog_path`` is
set, ticks are batched and flushed with ``ParquetDataCatalog.write_data`` on a timer
(same interval/batch env as ``RECORDER_FLUSH_INTERVAL_MS`` / ``RECORDER_MAX_BATCH_ROWS``).

Live-only (own WS connection); backtests replay ``LiquidationTick`` from the catalog.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import websockets

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import DataType

from liquidations import binance_to_nautilus
from recorders.data_types import LiquidationTick

WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
_CATALOG_FLUSH_TIMER = "liq_catalog_flush"


class LiquidationFeedActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...] = ()
    ws_url: str = WS_URL
    catalog_path: str | None = None
    catalog_flush_interval_sec: float = 1.0
    catalog_max_batch: int = 5_000


class LiquidationFeedActor(Actor):
    """Publish ``LiquidationTick`` from Binance ``!forceOrder@arr`` (filtered to config symbols)."""

    def __init__(self, config: LiquidationFeedActorConfig) -> None:
        super().__init__(config)
        self._symbols: set[str] = set(config.instrument_ids)
        self._task: asyncio.Task | None = None
        self._buffer: list[LiquidationTick] = []
        self._catalog = None
        if config.catalog_path:
            from catalog import get_catalog

            self._catalog = get_catalog(config.catalog_path)

    def on_start(self) -> None:
        self._task = asyncio.create_task(self._run_ws())
        if self._catalog is not None:
            self.clock.set_timer(
                _CATALOG_FLUSH_TIMER,
                timedelta(seconds=float(self.config.catalog_flush_interval_sec)),
                callback=self._on_catalog_flush_timer,
            )
            self.log.info(
                f"LiquidationTick catalog flush every "
                f"{self.config.catalog_flush_interval_sec}s → {self.config.catalog_path}",
            )

    def on_stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        if self._catalog is not None:
            self.clock.cancel_timer(_CATALOG_FLUSH_TIMER)
            self._flush_catalog()

    def _on_catalog_flush_timer(self, _event) -> None:
        self._flush_catalog()

    def _flush_catalog(self) -> None:
        if self._catalog is None or not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        # Nautilus ParquetDataCatalog expects non-decreasing ts_init.
        batch.sort(key=lambda tick: tick.ts_init)
        try:
            self._catalog.write_data(batch)
        except Exception as e:  # noqa: BLE001 — re-queue batch on transient catalog errors
            self.log.error(f"LiquidationTick catalog flush failed: {e!r}")
            self._buffer = batch + self._buffer

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
            self._emit_tick(
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

    def _emit_tick(self, tick: LiquidationTick) -> None:
        self.publish_data(DataType(LiquidationTick), tick)
        if self._catalog is None:
            return
        self._buffer.append(tick)
        if len(self._buffer) >= int(self.config.catalog_max_batch):
            self._flush_catalog()
