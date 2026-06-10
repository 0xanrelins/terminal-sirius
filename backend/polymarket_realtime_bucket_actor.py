"""Aggregate 1s/5s UP mid from Polymarket quote ticks → WS bar (Binance RealtimeBucketActor pattern)."""

from __future__ import annotations

import asyncio
import queue
import time
from dataclasses import dataclass
from typing import Optional

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.messages import ActivePolymarketMarket
from adapters.polymarket.quote_bridge_actor import should_broadcast_quote
from adapters.polymarket.rolling import series_symbol
from bar_time import bar_open_time_ns

REALTIME_INTERVALS = ("1s", "5s")
FORMING_BAR_THROTTLE_NS = 500_000_000  # 500ms — 2 forming bar emits/sec
HEARTBEAT_INTERVAL_SEC = 1.0


def _price_as_float(p) -> float:
    if hasattr(p, "as_double"):
        return float(p.as_double())
    return float(p)


def _quote_mid(tick: QuoteTick) -> float | None:
    bid = _price_as_float(tick.bid_price)
    ask = _price_as_float(tick.ask_price)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    mid = max(bid, ask)
    return mid if mid > 0 else None


@dataclass
class _BucketOhlcv:
    time: int
    open: float
    high: float
    low: float
    close: float

    @property
    def volume(self) -> float:
        return 0.0


@dataclass
class _StreamState:
    bucket: _BucketOhlcv | None = None
    last_forming_emit_ns: int = 0


class PolymarketRealtimeBucketActorConfig(ActorConfig, frozen=True):
    series: tuple[str, ...]


class PolymarketRealtimeBucketActor(Actor):
    """Subscribe to UP quote ticks after ``PolymarketQuoteBridgeActor`` loads instruments."""

    def __init__(self, config: PolymarketRealtimeBucketActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._series = set(config.series)
        self._queue = data_queue
        self._series_slugs: dict[str, str] = {}
        self._slug_yes_iid: dict[str, InstrumentId] = {}
        self._yes_iid_to_series: dict[str, str] = {}
        self._iid_to_slug: dict[str, str] = {}
        self._states: dict[tuple[str, str], _StreamState] = {}
        self._last_mid: dict[str, float] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def _state(self, symbol: str, interval: str) -> _StreamState:
        key = (symbol, interval)
        st = self._states.get(key)
        if st is None:
            st = _StreamState()
            self._states[key] = st
        return st

    def _clear_series_bucket_states(self, series: str) -> None:
        symbol = series_symbol(series)
        for interval in REALTIME_INTERVALS:
            self._states.pop((symbol, interval), None)
        self._last_mid.pop(symbol, None)

    def on_start(self) -> None:
        self.subscribe_data(DataType(ActivePolymarketMarket))
        if self._series:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def on_stop(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()

    def on_dispose(self) -> None:
        self.on_stop()

    def on_data(self, data) -> None:
        if isinstance(data, ActivePolymarketMarket):
            self._on_active_market(data)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                now_ns = time.time_ns()
                for series in list(self._series_slugs):
                    symbol = series_symbol(series)
                    mid = self._last_mid.get(symbol)
                    if mid is None:
                        continue
                    for interval in REALTIME_INTERVALS:
                        self._on_mid(symbol, interval, mid, now_ns)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.warning(f"PM bucket heartbeat error: {e!r}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    def _on_active_market(self, data: ActivePolymarketMarket) -> None:
        """Attach quote subscription only after bridge registered instruments in cache."""
        series = str(data.series or "")
        slug = str(data.slug or "")
        if not series or series not in self._series or not slug:
            return

        tracked = self._series_slugs.get(series)
        if tracked is not None and tracked != slug:
            self._drop_slug(tracked)
            self._clear_series_bucket_states(series)

        self._series_slugs[series] = slug
        yes_iid = data.instrument_id
        if self.cache.instrument(yes_iid) is None:
            self.log.warning(
                f"PM bucket: defer {series!r} slug={slug!r} — instrument not in cache yet",
            )
            return
        self._attach_yes_quotes(yes_iid, series=series, slug=slug)

    def _attach_yes_quotes(self, yes_iid: InstrumentId, *, series: str, slug: str) -> None:
        existing = self._slug_yes_iid.get(slug)
        if existing == yes_iid:
            return
        if existing is not None and existing != yes_iid:
            self._drop_slug(slug)

        iid_str = str(yes_iid)
        self.subscribe_quote_ticks(yes_iid)
        self._slug_yes_iid[slug] = yes_iid
        self._yes_iid_to_series[iid_str] = series
        self._iid_to_slug[iid_str] = slug
        self.log.info(f"PM bucket: subscribed UP quotes for {series!r} slug={slug!r}")

    def _drop_slug(self, slug: str) -> None:
        """Drop local slug tracking only — do not unsubscribe_quote_ticks.

        Quote ticks are shared with PolymarketQuoteBridgeActor on the same
        TradingNode; unsubscribing here would kill the price widget WS feed.
        """
        iid = self._slug_yes_iid.pop(slug, None)
        if iid is None:
            return
        iid_str = str(iid)
        self._yes_iid_to_series.pop(iid_str, None)
        self._iid_to_slug.pop(iid_str, None)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        iid_str = str(tick.instrument_id)
        series = self._yes_iid_to_series.get(iid_str)
        if series is None:
            return
        slug = self._iid_to_slug.get(iid_str)
        if slug is None:
            return
        meta = {"slug": slug, "token": "yes", "series": series}
        if not should_broadcast_quote(meta, self._series_slugs):
            return
        mid = _quote_mid(tick)
        if mid is None:
            return

        symbol = series_symbol(series)
        self._last_mid[symbol] = mid
        ts_ns = tick.ts_event
        for interval in REALTIME_INTERVALS:
            self._on_mid(symbol, interval, mid, ts_ns)

    def _on_mid(self, symbol: str, interval: str, price: float, ts_ns: int) -> None:
        bucket_time = bar_open_time_ns(ts_ns, interval)
        st = self._state(symbol, interval)
        prev = st.bucket

        if prev is not None and bucket_time != prev.time:
            self._emit_bar(symbol, interval, prev, ts_ns)
            st.bucket = None
            prev = None

        if st.bucket is None or st.bucket.time != bucket_time:
            st.bucket = _BucketOhlcv(
                time=bucket_time,
                open=price,
                high=price,
                low=price,
                close=price,
            )
        else:
            b = st.bucket
            b.high = max(b.high, price)
            b.low = min(b.low, price)
            b.close = price

        now_ns = time.time_ns()
        if now_ns - st.last_forming_emit_ns >= FORMING_BAR_THROTTLE_NS:
            st.last_forming_emit_ns = now_ns
            if st.bucket is not None:
                self._emit_bar(symbol, interval, st.bucket, ts_ns)

    def _emit_bar(
        self,
        symbol: str,
        interval: str,
        bucket: _BucketOhlcv,
        ts_ns: int,
    ) -> None:
        self._enqueue(
            {
                "type": "bar",
                "symbol": symbol,
                "interval": interval,
                "time": bucket.time,
                "open": str(bucket.open),
                "high": str(bucket.high),
                "low": str(bucket.low),
                "close": str(bucket.close),
                "volume": str(bucket.volume),
                "ts": ts_ns,
            }
        )
