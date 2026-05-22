"""Signal dedupe helpers."""
from engines.signal_state import events_indicate_bet_opened


def test_bet_open_only():
    assert events_indicate_bet_opened([{"type": "live_bet_open"}])
    assert not events_indicate_bet_opened([{"type": "live_order_error"}])
    assert not events_indicate_bet_opened([])
