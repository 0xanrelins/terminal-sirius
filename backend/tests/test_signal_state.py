"""Shared strategy signal derivation tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.signal_state import SignalInputs  # noqa: E402
from strategies.signal_state import compute_derived  # noqa: E402
from strategies.signal_state import entry_direction  # noqa: E402


def _inputs(**kwargs) -> SignalInputs:
    base = dict(
        vwap=100.0,
        slope=0.01,
        low_zone=95.0,
        high_zone=105.0,
        last_price=94.0,
        liq_long_trigger=True,
        liq_short_trigger=False,
        slope_eps=0.05,
        vwap_ready=True,
    )
    base.update(kwargs)
    return SignalInputs(**base)


def test_entry_direction_long_when_zone_and_liq():
    assert entry_direction(_inputs()) == "LONG"


def test_entry_direction_short():
    assert (
        entry_direction(
            _inputs(
                slope=-0.06,
                last_price=106.0,
                liq_long_trigger=False,
                liq_short_trigger=True,
            )
        )
        == "SHORT"
    )


def test_entry_direction_none_without_liq_trigger():
    assert entry_direction(_inputs(liq_long_trigger=False)) is None


def test_compute_derived_hold_when_not_ready():
    d = compute_derived(_inputs(vwap_ready=False))
    assert d.decision == "HOLD"
    assert d.long_zone is True


def test_compute_derived_long_decision():
    d = compute_derived(_inputs())
    assert d.decision == "LONG"
    assert d.in_range is True
    assert d.long_zone is True
    assert d.short_zone is False
