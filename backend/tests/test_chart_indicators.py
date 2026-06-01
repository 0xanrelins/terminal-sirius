"""Indicator math parity with frontend chartIndicators.ts."""

from chart_indicators import (
    OhlcvBar,
    calculate_ema,
    calculate_rolling_vwap,
    calculate_session_vwap_points,
    EmaState,
    RollingVwapState,
    SessionVwapState,
    session_bucket_open,
)


def _bars(n: int, *, vol: float = 10.0) -> list[OhlcvBar]:
    out: list[OhlcvBar] = []
    for i in range(n):
        c = 100.0 + i
        out.append(
            OhlcvBar(
                time=1_700_000_000 + i * 5,
                open=c,
                high=c + 1,
                low=c - 1,
                close=c,
                volume=vol,
            )
        )
    return out


def test_session_bucket_open_5s_period_20() -> None:
    # 20 * 5s = 100s sessions
    t = 1_700_000_042
    assert session_bucket_open(t, "5s", 20) == (t // 100) * 100


def test_ema_warmup_then_values() -> None:
    closes = [float(i) for i in range(25)]
    batch = calculate_ema(closes, 20)
    assert all(v is None for v in batch[:19])
    assert batch[19] is not None

    inc = EmaState(period=20)
    for i, c in enumerate(closes):
        v = inc.on_bar_close(c)
        if i < 19:
            assert v is None
        else:
            assert v == batch[i]


def test_rolling_vwap_matches_batch() -> None:
    bars = _bars(25)
    batch = calculate_rolling_vwap(bars, 20)
    inc = RollingVwapState(period=20)
    for i, b in enumerate(bars):
        v = inc.on_bar_close(b)
        assert v == batch[i]


def test_session_vwap_resets_on_bucket_change() -> None:
    bars = _bars(30)
    # force two session buckets by spacing bars across 100s boundary on 5s
    bars[0] = OhlcvBar(time=100, open=1, high=2, low=0.5, close=1, volume=5)
    bars[10] = OhlcvBar(time=250, open=2, high=3, low=1, close=2, volume=5)

    batch = calculate_session_vwap_points(bars, 20, "5s")
    inc = SessionVwapState(period=20, chart_interval="5s")
    for b, (t, expected) in zip(bars, batch):
        v, _reset = inc.on_bar_close(b)
        assert v == expected
        assert t == b.time


def test_incremental_ema_matches_last_bar() -> None:
    bars = _bars(30)
    closes = [b.close for b in bars]
    batch = calculate_ema(closes, 20)
    inc = EmaState(period=20)
    last = None
    for b in bars:
        last = inc.on_bar_close(b.close)
    assert last == batch[-1]
