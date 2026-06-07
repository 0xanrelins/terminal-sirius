"""Entry guard: no OPEN on expired Polymarket 15m windows."""

from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from strategies.terminal_sirius_strategy import TerminalSiriusStrategy
from strategies.terminal_sirius_strategy import TerminalSiriusStrategyConfig


def _strategy(clock: MagicMock) -> TerminalSiriusStrategy:
    cfg = TerminalSiriusStrategyConfig(
        binance_instruments=("BTCUSDT-PERP.BINANCE",),
        polymarket_series=("btc-updown-15m",),
        backtest_mode=True,
    )
    s = TerminalSiriusStrategy(cfg)
    patcher = patch.object(type(s), "clock", new_callable=PropertyMock, return_value=clock)
    patcher.start()
    s._clock_patcher = patcher  # keep reference for test lifetime
    return s


def test_entry_allowed_inside_window():
    clock = MagicMock()
    s = _strategy(clock)
    window_start = 1_780_814_700
    clock.timestamp_ns.return_value = (window_start + 60) * 1_000_000_000
    inst = MagicMock()
    inst.expiration_ns = (window_start + 900 + 10) * 1_000_000_000
    inst.info = {"market_slug": f"btc-updown-15m-{window_start}"}
    assert s._entry_allowed(inst) is True


def test_entry_blocked_after_window_end_before_expiration_grace():
    clock = MagicMock()
    s = _strategy(clock)
    window_start = 1_780_814_700
    window_end = window_start + 900
    clock.timestamp_ns.return_value = (window_end + 5) * 1_000_000_000
    inst = MagicMock()
    inst.expiration_ns = (window_end + 10) * 1_000_000_000
    inst.info = {"market_slug": f"btc-updown-15m-{window_start}"}
    assert s._entry_allowed(inst) is False


def test_entry_blocked_at_expiration_ns():
    clock = MagicMock()
    s = _strategy(clock)
    exp_ns = 9_000_000_000_000
    clock.timestamp_ns.return_value = exp_ns
    inst = MagicMock()
    inst.expiration_ns = exp_ns
    inst.info = {}
    assert s._entry_allowed(inst) is False
