"""Bet window alignment: liq bar → next 15m Poly window."""
from simulation.config import WINDOW_SEC, bet_window_open, next_window_open


def test_bet_window_is_bar_after_liq():
    liq = 1_779_111_900  # 15m bar open
    assert bet_window_open(liq) == liq + WINDOW_SEC


def test_late_signal_does_not_shift_bet_window():
    """Wall-clock signal time must not push the bet one window ahead of the liq bar."""
    liq = 1_779_111_900
    # Signal recorded after the liq bar closed (e.g. startup sync / delayed WS)
    signal_after_liq_bar = liq + WINDOW_SEC + 1
    assert bet_window_open(liq) == liq + WINDOW_SEC
    assert next_window_open(signal_after_liq_bar) == liq + 2 * WINDOW_SEC
    assert bet_window_open(liq) != next_window_open(signal_after_liq_bar)
