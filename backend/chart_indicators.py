"""Chart indicator math — mirrors frontend/src/lib/chartIndicators.ts + barTime.ts."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from bar_time import INTERVAL_SECONDS

DEFAULT_EMA_PERIOD = 180
DEFAULT_VWAP_PERIOD = 180
DEFAULT_ROLLING_VWAP_PERIOD = 180


@dataclass(frozen=True)
class OhlcvBar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def session_bucket_open(bar_time_sec: int, chart_interval: str, period_bars: int) -> int:
    bar_sec = INTERVAL_SECONDS.get(chart_interval, 60)
    session_sec = max(1, int(period_bars)) * bar_sec
    return (bar_time_sec // session_sec) * session_sec


def calculate_ema(closes: list[float], period: int) -> list[float | None]:
    """EMA with None during warmup (matches frontend whitespace)."""
    out: list[float | None] = []
    mult = 2 / (period + 1)
    ema: float | None = None
    p = max(1, int(period))

    for i, close in enumerate(closes):
        if i < p - 1:
            out.append(None)
        elif ema is None:
            seed = sum(closes[i - p + 1 : i + 1]) / p
            ema = seed
            out.append(ema)
        else:
            ema = (close - ema) * mult + ema
            out.append(ema)
    return out


def calculate_rolling_vwap(bars: list[OhlcvBar], period: int) -> list[float | None]:
    out: list[float | None] = []
    p = max(1, int(period))
    for i in range(len(bars)):
        if i < p - 1:
            out.append(None)
            continue
        sum_pv = 0.0
        sum_v = 0.0
        for j in range(i - p + 1, i + 1):
            b = bars[j]
            vol = b.volume
            if vol <= 0:
                continue
            tp = (b.high + b.low + b.close) / 3
            sum_pv += tp * vol
            sum_v += vol
        out.append(sum_pv / sum_v if sum_v > 0 else None)
    return out


def calculate_session_vwap_points(
    bars: list[OhlcvBar],
    period: int,
    chart_interval: str,
) -> list[tuple[int, float | None]]:
    """One (time, value) per bar; None when no volume in session."""
    p = max(1, int(period))
    out: list[tuple[int, float | None]] = []
    sum_pv = 0.0
    sum_v = 0.0
    current_bucket: int | None = None

    for b in bars:
        bucket = session_bucket_open(b.time, chart_interval, p)
        if current_bucket is not None and bucket != current_bucket:
            sum_pv = 0.0
            sum_v = 0.0
        current_bucket = bucket

        vol = b.volume
        if vol > 0:
            tp = (b.high + b.low + b.close) / 3
            sum_pv += tp * vol
            sum_v += vol
        out.append((b.time, sum_pv / sum_v if sum_v > 0 else None))
    return out


@dataclass
class EmaState:
    period: int
    _closes: list[float] = field(default_factory=list)
    _ema: float | None = None

    def on_bar_close(self, close: float) -> float | None:
        p = max(1, int(self.period))
        self._closes.append(close)
        i = len(self._closes) - 1
        mult = 2 / (p + 1)

        if i < p - 1:
            return None
        if self._ema is None:
            self._ema = sum(self._closes[-p:]) / p
            return self._ema
        self._ema = (close - self._ema) * mult + self._ema
        return self._ema


@dataclass
class RollingVwapState:
    period: int
    _bars: deque[OhlcvBar] = field(init=False)

    def __post_init__(self) -> None:
        self._bars = deque(maxlen=max(1, int(self.period)))

    def on_bar_close(self, bar: OhlcvBar) -> float | None:
        p = max(1, int(self.period))
        self._bars.append(bar)
        if len(self._bars) < p:
            return None
        sum_pv = 0.0
        sum_v = 0.0
        for b in self._bars:
            vol = b.volume
            if vol <= 0:
                continue
            tp = (b.high + b.low + b.close) / 3
            sum_pv += tp * vol
            sum_v += vol
        return sum_pv / sum_v if sum_v > 0 else None


@dataclass
class SessionVwapState:
    period: int
    chart_interval: str
    _sum_pv: float = 0.0
    _sum_v: float = 0.0
    _current_bucket: int | None = None

    def on_bar_close(self, bar: OhlcvBar) -> tuple[float | None, bool]:
        """Return (value, segment_reset)."""
        bucket = session_bucket_open(bar.time, self.chart_interval, self.period)
        segment_reset = self._current_bucket is not None and bucket != self._current_bucket
        if segment_reset:
            self._sum_pv = 0.0
            self._sum_v = 0.0
        self._current_bucket = bucket

        vol = bar.volume
        if vol > 0:
            tp = (bar.high + bar.low + bar.close) / 3
            self._sum_pv += tp * vol
            self._sum_v += vol
        if self._sum_v > 0:
            return self._sum_pv / self._sum_v, segment_reset
        return None, segment_reset
