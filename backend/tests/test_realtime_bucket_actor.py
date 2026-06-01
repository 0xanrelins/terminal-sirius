"""RealtimeBucketActor bucket rollover and WS payloads."""

import queue

from realtime_bucket_actor import RealtimeBucketActor, RealtimeBucketActorConfig


def test_bucket_rollover_emits_bar_and_indicators() -> None:
    q: queue.Queue = queue.Queue()
    cfg = RealtimeBucketActorConfig(
        component_id="RealtimeBucket-001",
        instrument_ids=("BTCUSDT-PERP.BINANCE",),
        ema_period=3,
        vwap_period=2,
        rolling_vwap_period=2,
    )
    actor = RealtimeBucketActor(config=cfg, data_queue=q)
    symbol = "BTCUSDT-PERP.BINANCE"

    # Bucket at t=10 for 1s interval
    actor._on_tick(symbol, "1s", 100.0, 1.0, 10_000_000_000)
    actor._on_tick(symbol, "1s", 101.0, 2.0, 10_500_000_000)

    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())

    # forming bars only so far
    assert any(m["type"] == "bar" and m["interval"] == "1s" for m in msgs)

    # Roll to next second — closes bucket at t=10
    actor._on_tick(symbol, "1s", 102.0, 1.0, 11_000_000_000)
    while not q.empty():
        msgs.append(q.get_nowait())

    closed_bars = [
        m
        for m in msgs
        if m["type"] == "bar" and m["interval"] == "1s" and m["time"] == 10
    ]
    assert len(closed_bars) >= 1
    assert closed_bars[-1]["close"] == "101.0"

    indicators = [m for m in msgs if m["type"] == "indicator" and m["time"] == 10]
    kinds = {m["indicator"] for m in indicators}
    assert kinds == {"ema", "vwap", "rolling_vwap"}

    ema_msgs = [m for m in indicators if m["indicator"] == "ema"]
    assert len(ema_msgs) == 1
    # period=3: first close at index 2 → no value yet for first closed bar at t=10?
    # closes: 100 at tick1, 101 at tick2 — on close at t=10 we have 2 closes in bucket but
    # indicator runs on bar close with close=101 — ema needs 3 bars, still warmup
    assert "value" not in ema_msgs[0]
