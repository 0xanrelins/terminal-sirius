"""Liquidation verdict pure logic tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.liquidation_verdict_logic import (  # noqa: E402
    OpenVerdictEvent,
    VerdictEventIdFactory,
    dominance_ratio,
    update_open_event,
    verdict_passes_gates,
)


def test_verdict_event_id_uses_order_id():
    factory = VerdictEventIdFactory()
    event_id = factory.make(
        "BTCUSDT-PERP.BINANCE",
        "LONG",
        1_000_000_000,
        order_id=42,
    )
    assert event_id == "verdict-1000000000-BTC-LONG-42"


def test_verdict_event_id_sequences_same_ms_without_order_id():
    factory = VerdictEventIdFactory()
    ids = [
        factory.make("BTCUSDT-PERP.BINANCE", "LONG", 1_000_000_000)
        for _ in range(3)
    ]
    assert len(set(ids)) == 3
    assert ids[0] == "verdict-1000000000-BTC-LONG"
    assert ids[1] == "verdict-1000000000-BTC-LONG-s1"
    assert ids[2] == "verdict-1000000000-BTC-LONG-s2"


def test_dominance_ratio_requires_both_sides():
    assert dominance_ratio(0.6, 0.0) == 0.0
    assert abs(dominance_ratio(0.6, 0.1) - 6.0) < 1e-9


def test_recovery_wins_when_recovery_threshold_hits_first():
    event = OpenVerdictEvent(
        event_id="t-1",
        symbol="BTCUSDT-PERP.BINANCE",
        liq_side="LONG",
        notional=100_000.0,
        event_price=100.0,
        event_ts_ns=0,
    )
    completed = update_open_event(
        event,
        100.21,
        1_000_000_000,
        liq_move_threshold_pct=0.2,
        recovery_move_threshold_pct=0.2,
    )
    assert completed is not None
    assert completed.winner == "recovery"
    assert completed.completion_reason == "recovery_threshold"
    assert completed.recovery_move_pct >= 0.2
    assert completed.status == "completed"


def test_liquidation_wins_when_liq_threshold_hits_first():
    event = OpenVerdictEvent(
        event_id="t-2",
        symbol="BTCUSDT-PERP.BINANCE",
        liq_side="LONG",
        notional=100_000.0,
        event_price=100.0,
        event_ts_ns=0,
    )
    completed = update_open_event(
        event,
        99.79,
        1_000_000_000,
        liq_move_threshold_pct=0.2,
        recovery_move_threshold_pct=0.2,
    )
    assert completed is not None
    assert completed.winner == "liquidation"
    assert completed.completion_reason == "liq_threshold"
    assert completed.liq_move_pct >= 0.2
    assert completed.status == "completed"


def test_verdict_passes_gates_recovery():
    from strategies.liquidation_verdict_logic import CompletedVerdict

    verdict = CompletedVerdict(
        event_id="t-1",
        symbol="BTCUSDT-PERP.BINANCE",
        liq_side="LONG",
        notional=100_000.0,
        event_price=100.0,
        event_ts_ns=0,
        winner="recovery",
        liq_move_pct=0.05,
        recovery_move_pct=0.3,
        dominance_ratio=6.0,
        time_to_dominance_sec=18.0,
        area_bias=0.4,
        status="completed",
        completion_reason="recovery_threshold",
    )
    assert verdict_passes_gates(
        verdict,
        min_recovery_move_pct=0.2,
        max_time_to_completion_sec=450.0,
        min_area_bias=0.0,
    )
