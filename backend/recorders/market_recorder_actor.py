"""Actor which aggregates market streams into recorder custom data."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType, QuoteTick, TradeTick
from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.rolling import active_rolling_slugs
from recorders.catalog_writer import CatalogWriter
from recorders.data_types import (
    BinanceLiquidationEvent,
    BinanceSecondPrice,
    PolymarketSecondPrice,
)


@dataclass
class _BinanceSecondBucket:
    second: int
    last_price: float


@dataclass
class _PolymarketSecondBucket:
    second: int
    up_last_price: float | None = None
    down_last_price: float | None = None


class MarketRecorderActorConfig(ActorConfig, frozen=True):
    binance_instruments: tuple[str, ...]
    polymarket_series: tuple[str, ...]


class MarketRecorderActor(Actor):
    """Subscribe to market/custom streams and forward snapshots to CatalogWriter."""

    def __init__(self, config: MarketRecorderActorConfig, writer: CatalogWriter) -> None:
        super().__init__(config)
        self._writer = writer
        self._binance_iids = tuple(
            InstrumentId.from_str(value) for value in config.binance_instruments
        )
        self._series = tuple(config.polymarket_series)
        self._binance_buckets: dict[str, _BinanceSecondBucket] = {}
        self._poly_buckets: dict[str, _PolymarketSecondBucket] = {}
        self._poly_token_role: dict[str, tuple[str, str]] = {}
        self._poly_slug_iids: dict[str, list[InstrumentId]] = {}
        self._rotation_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._enqueue_dropped = 0

    def on_start(self) -> None:
        # LiquidationActor publishes BinanceLiquidationEvent on the message bus.
        self.subscribe_data(DataType(BinanceLiquidationEvent))
        for iid in self._binance_iids:
            self.subscribe_trade_ticks(iid)
        asyncio.create_task(self._bootstrap_polymarket_quotes())
        self._rotation_task = asyncio.create_task(self._rotation_loop())
        self._health_task = asyncio.create_task(self._health_loop())

    def on_stop(self) -> None:
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        self._flush_open_buckets()

    def on_dispose(self) -> None:
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        self._flush_open_buckets()

    def on_trade_tick(self, tick: TradeTick) -> None:
        second = int(tick.ts_event // 1_000_000_000)
        symbol = str(tick.instrument_id)
        price = float(tick.price.as_double()) if hasattr(tick.price, "as_double") else float(tick.price)
        cur = self._binance_buckets.get(symbol)
        if cur is None:
            self._binance_buckets[symbol] = _BinanceSecondBucket(second=second, last_price=price)
            return
        if second == cur.second:
            cur.last_price = price
            return

        self._enqueue(
            BinanceSecondPrice(
                ts_event=cur.second * 1_000_000_000,
                ts_init=tick.ts_event,
                symbol=symbol,
                last_price=cur.last_price,
            ),
        )
        self._binance_buckets[symbol] = _BinanceSecondBucket(second=second, last_price=price)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        key = str(tick.instrument_id)
        role = self._poly_token_role.get(key)
        if role is None:
            return
        market, token = role
        second = int(tick.ts_event // 1_000_000_000)
        bid = float(tick.bid_price.as_double()) if hasattr(tick.bid_price, "as_double") else float(tick.bid_price)
        ask = float(tick.ask_price.as_double()) if hasattr(tick.ask_price, "as_double") else float(tick.ask_price)
        mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else max(bid, ask)
        if mid <= 0:
            return

        cur = self._poly_buckets.get(market)
        if cur is None:
            cur = _PolymarketSecondBucket(second=second)
            self._poly_buckets[market] = cur
        if cur.second != second:
            self._emit_poly_bucket(market=market, bucket=cur, ts_init=tick.ts_event)
            cur = _PolymarketSecondBucket(second=second)
            self._poly_buckets[market] = cur

        if token == "up":
            cur.up_last_price = mid
        else:
            cur.down_last_price = mid

    def on_data(self, data: Data) -> None:
        if not isinstance(data, BinanceLiquidationEvent):
            return
        # Event-level passthrough for append-only liquidation parquet.
        self._enqueue(data)

    async def _bootstrap_polymarket_quotes(self) -> None:
        await asyncio.sleep(1.0)
        await self._refresh_polymarket_subscriptions()

    async def _rotation_loop(self) -> None:
        while True:
            try:
                await self._refresh_polymarket_subscriptions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.warning(f"Recorder rotation error: {e!r}")
            await asyncio.sleep(20.0)

    async def _health_loop(self) -> None:
        while True:
            try:
                if not self._writer.is_healthy():
                    stats = self._writer.stats_snapshot()
                    self.log.error(f"Recorder writer unhealthy: {stats}")
                elif self._enqueue_dropped > 0 and self._enqueue_dropped % 100 == 0:
                    stats = self._writer.stats_snapshot()
                    self.log.warning(f"Recorder dropped={self._enqueue_dropped} stats={stats}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.warning(f"Recorder health check error: {e!r}")
            await asyncio.sleep(5.0)

    async def _refresh_polymarket_subscriptions(self) -> None:
        expected_slugs: set[str] = set()
        for series in self._series:
            current, nxt = active_rolling_slugs(series)
            expected_slugs.add(current)
            expected_slugs.add(nxt)
            await self._subscribe_slug(series=series, slug=current)
            await self._subscribe_slug(series=series, slug=nxt)

        for slug in list(self._poly_slug_iids):
            if slug in expected_slugs:
                continue
            for iid in self._poly_slug_iids.pop(slug):
                try:
                    self.unsubscribe_quote_ticks(iid)
                except Exception:
                    pass
                self._poly_token_role.pop(str(iid), None)

    async def _subscribe_slug(self, *, series: str, slug: str) -> None:
        if slug in self._poly_slug_iids:
            return
        from nautilus_trader.adapters.polymarket.loaders import PolymarketDataLoader

        market = series.split("-")[0].upper()
        loaded: list[InstrumentId] = []
        for token_index, token in ((0, "up"), (1, "down")):
            try:
                loader = await PolymarketDataLoader.from_market_slug(
                    slug,
                    token_index=token_index,
                )
                instrument = loader.instrument
            except Exception as e:
                self.log.warning(f"Recorder skip slug={slug!r} token={token}: {e!r}")
                continue
            iid = instrument.id
            if self.cache.instrument(iid) is None:
                self.cache.add_instrument(instrument)
            self.request_instrument(iid)
            self.subscribe_quote_ticks(iid)
            self._poly_token_role[str(iid)] = (market, token)
            loaded.append(iid)
        if loaded:
            self._poly_slug_iids[slug] = loaded

    def _emit_poly_bucket(self, *, market: str, bucket: _PolymarketSecondBucket, ts_init: int) -> None:
        if bucket.up_last_price is None or bucket.down_last_price is None:
            return
        self._enqueue(
            PolymarketSecondPrice(
                ts_event=bucket.second * 1_000_000_000,
                ts_init=ts_init,
                market=market,
                up_last_price=bucket.up_last_price,
                down_last_price=bucket.down_last_price,
            ),
        )

    def _enqueue(self, item: Data) -> None:
        ok = self._writer.enqueue(item)
        if ok:
            return
        self._enqueue_dropped += 1
        if self._enqueue_dropped <= 3 or self._enqueue_dropped % 50 == 0:
            stats = self._writer.stats_snapshot()
            self.log.warning(f"Recorder enqueue drop count={self._enqueue_dropped} stats={stats}")

    def _flush_open_buckets(self) -> None:
        for symbol, bucket in self._binance_buckets.items():
            self._enqueue(
                BinanceSecondPrice(
                    ts_event=bucket.second * 1_000_000_000,
                    ts_init=bucket.second * 1_000_000_000,
                    symbol=symbol,
                    last_price=bucket.last_price,
                ),
            )
        self._binance_buckets.clear()
        for market, bucket in self._poly_buckets.items():
            self._emit_poly_bucket(
                market=market,
                bucket=bucket,
                ts_init=bucket.second * 1_000_000_000,
            )
        self._poly_buckets.clear()
