"""Aggregate 1s/5s OHLCV from trade ticks and emit bar + indicator WS messages."""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId

from bar_time import bar_open_time_ns
from chart_indicators import (
    DEFAULT_EMA_PERIOD,
    DEFAULT_ROLLING_VWAP_PERIOD,
    DEFAULT_VWAP_PERIOD,
    EmaState,
    OhlcvBar,
    RollingVwapState,
    SessionVwapState,
)

REALTIME_INTERVALS = ("1s", "5s")
FORMING_BAR_THROTTLE_NS = 500_000_000  # 500ms — 2 forming bar emits/sec


class RealtimeBucketActorConfig(ActorConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    ema_period: int = DEFAULT_EMA_PERIOD
    vwap_period: int = DEFAULT_VWAP_PERIOD
    rolling_vwap_period: int = DEFAULT_ROLLING_VWAP_PERIOD


@dataclass
class _BucketOhlcv:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_bar(self) -> OhlcvBar:
        return OhlcvBar(
            time=self.time,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


@dataclass
class _StreamState:
    bucket: _BucketOhlcv | None = None
    last_forming_emit_ns: int = 0
    ema: EmaState = field(default_factory=lambda: EmaState(period=DEFAULT_EMA_PERIOD))
    session_vwap: SessionVwapState = field(
        default_factory=lambda: SessionVwapState(
            period=DEFAULT_VWAP_PERIOD, chart_interval="1s"
        )
    )
    rolling_vwap: RollingVwapState = field(
        default_factory=lambda: RollingVwapState(period=DEFAULT_ROLLING_VWAP_PERIOD)
    )


class RealtimeBucketActor(Actor):
    """Subscribes to trade ticks; builds 1s/5s bars and incremental indicators for WS."""

    def __init__(self, config: RealtimeBucketActorConfig, data_queue: queue.Queue) -> None:
        super().__init__(config)
        self._instrument_ids = [InstrumentId.from_str(i) for i in config.instrument_ids]
        self._queue = data_queue
        self._ema_period = config.ema_period
        self._vwap_period = config.vwap_period
        self._rolling_period = config.rolling_vwap_period
        self._states: dict[tuple[str, str], _StreamState] = {}

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def _state(self, symbol: str, interval: str) -> _StreamState:
        key = (symbol, interval)
        st = self._states.get(key)
        if st is None:
            st = _StreamState(
                ema=EmaState(period=self._ema_period),
                session_vwap=SessionVwapState(
                    period=self._vwap_period, chart_interval=interval
                ),
                rolling_vwap=RollingVwapState(period=self._rolling_period),
            )
            self._states[key] = st
        return st

    def on_start(self) -> None:
        for iid in self._instrument_ids:
            self.subscribe_trade_ticks(iid)

    def on_trade_tick(self, tick: TradeTick) -> None:
        symbol = str(tick.instrument_id)
        price = float(tick.price)
        size = float(tick.size)
        ts_ns = tick.ts_event

        for interval in REALTIME_INTERVALS:
            self._on_tick(symbol, interval, price, size, ts_ns)

    def _on_tick(
        self,
        symbol: str,
        interval: str,
        price: float,
        size: float,
        ts_ns: int,
    ) -> None:
        bucket_time = bar_open_time_ns(ts_ns, interval)
        st = self._state(symbol, interval)
        prev = st.bucket

        if prev is not None and bucket_time != prev.time:
            self._emit_closed_bar(symbol, interval, prev, ts_ns)
            st.bucket = None
            prev = None

        if st.bucket is None or st.bucket.time != bucket_time:
            st.bucket = _BucketOhlcv(
                time=bucket_time,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=size,
            )
        else:
            b = st.bucket
            b.high = max(b.high, price)
            b.low = min(b.low, price)
            b.close = price
            b.volume += size

        now_ns = time.time_ns()
        if now_ns - st.last_forming_emit_ns >= FORMING_BAR_THROTTLE_NS:
            st.last_forming_emit_ns = now_ns
            self._emit_bar(symbol, interval, st.bucket, ts_ns)

    def _emit_closed_bar(
        self,
        symbol: str,
        interval: str,
        bucket: _BucketOhlcv,
        ts_ns: int,
    ) -> None:
        self._emit_bar(symbol, interval, bucket, ts_ns)
        bar = bucket.to_bar()
        st = self._state(symbol, interval)

        ema_val = st.ema.on_bar_close(bar.close)
        self._emit_indicator(symbol, interval, bucket.time, "ema", self._ema_period, ema_val)

        session_val, _reset = st.session_vwap.on_bar_close(bar)
        self._emit_indicator(
            symbol, interval, bucket.time, "vwap", self._vwap_period, session_val
        )

        rolling_val = st.rolling_vwap.on_bar_close(bar)
        self._emit_indicator(
            symbol,
            interval,
            bucket.time,
            "rolling_vwap",
            self._rolling_period,
            rolling_val,
        )

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

    def _emit_indicator(
        self,
        symbol: str,
        interval: str,
        time_sec: int,
        indicator: str,
        period: int,
        value: float | None,
    ) -> None:
        msg: dict = {
            "type": "indicator",
            "symbol": symbol,
            "interval": interval,
            "time": time_sec,
            "indicator": indicator,
            "period": period,
        }
        if value is not None:
            msg["value"] = str(value)
        self._enqueue(msg)
