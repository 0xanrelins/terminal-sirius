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
from pathlib import Path

import websockets

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import DataType

from liquidations import binance_to_nautilus
from recorders.data_types import LiquidationTick

WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
_CATALOG_FLUSH_TIMER = "liq_catalog_flush"
_LIQ_STATE_FILE = ".liq_stream_state.json"


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
        self._last_written_ts_init: int = 0
        self._state_file: Path | None = None
        if config.catalog_path:
            from catalog import get_catalog

            self._catalog = get_catalog(config.catalog_path)
            self._state_file = Path(self._catalog.path) / _LIQ_STATE_FILE

    def on_start(self) -> None:
        self._task = asyncio.create_task(self._run_ws())
        if self._catalog is not None:
            self._load_or_bootstrap_watermark()
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
        if self._last_written_ts_init > 0:
            original_len = len(batch)
            batch = [tick for tick in batch if tick.ts_init > self._last_written_ts_init]
            dropped = original_len - len(batch)
            if dropped:
                self.log.warning(
                    f"Dropped {dropped} stale LiquidationTick(s) at/below watermark "
                    f"{self._last_written_ts_init}",
                )
        if not batch:
            return
        try:
            self._catalog.write_data(batch)
        except Exception as e:  # noqa: BLE001 — re-queue batch on transient catalog errors
            self.log.error(f"LiquidationTick catalog flush failed: {e!r}")
            if "non-disjoint intervals" in str(e):
                self._refresh_watermark_from_catalog()
                if self._last_written_ts_init > 0:
                    before = len(batch)
                    batch = [tick for tick in batch if tick.ts_init > self._last_written_ts_init]
                    removed = before - len(batch)
                    if removed:
                        self.log.warning(
                            f"Removed {removed} overlapping LiquidationTick(s) after watermark refresh "
                            f"{self._last_written_ts_init}",
                        )
            self._buffer = batch + self._buffer
            return
        self._last_written_ts_init = batch[-1].ts_init
        self._save_watermark()

    def _load_or_bootstrap_watermark(self) -> None:
        if self._state_file is not None and self._state_file.exists():
            try:
                doc = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._last_written_ts_init = int(doc.get("last_written_ts_init", 0))
            except Exception:  # noqa: BLE001
                self._last_written_ts_init = 0
        if self._last_written_ts_init <= 0:
            self._refresh_watermark_from_catalog()
        self.log.info(f"LiquidationTick writer watermark: {self._last_written_ts_init}")

    def _refresh_watermark_from_catalog(self) -> None:
        if self._catalog is None:
            return
        try:
            last = self._catalog.query_last_timestamp(LiquidationTick)
        except Exception:  # noqa: BLE001
            return
        if last is None:
            return
        try:
            self._last_written_ts_init = int(last.value)
        except Exception:  # noqa: BLE001
            try:
                self._last_written_ts_init = int(last)
            except Exception:  # noqa: BLE001
                return
        self._save_watermark()

    def _save_watermark(self) -> None:
        if self._state_file is None:
            return
        payload = {"last_written_ts_init": int(self._last_written_ts_init)}
        self._state_file.write_text(json.dumps(payload), encoding="utf-8")

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
