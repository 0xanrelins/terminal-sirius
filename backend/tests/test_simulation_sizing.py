"""Unit tests for Polymarket bet sizing."""
import math

from simulation.sizing import compute_bet, compute_live_market_usd, pnl_for_outcome

_ABS = 0.001


def test_compute_bet_table() -> None:
    cases = [
        (0.50, 5, 2.5),
        (0.80, 5, 4.0),
        (0.20, 5, 1.0),
        (0.15, 7, 1.05),
    ]
    for price, expected_shares, expected_cost in cases:
        shares, cost = compute_bet(price, min_shares=5, min_usd=1.0)
        assert shares == expected_shares
        assert math.isclose(cost, expected_cost, abs_tol=_ABS)


def test_pnl_win() -> None:
    shares, cost = compute_bet(0.50, 5, 1.0)
    assert math.isclose(pnl_for_outcome(shares, cost, True), 2.5, abs_tol=_ABS)


def test_pnl_loss() -> None:
    shares, cost = compute_bet(0.50, 5, 1.0)
    assert math.isclose(pnl_for_outcome(shares, cost, False), -2.5, abs_tol=_ABS)


def test_next_window_open() -> None:
    from simulation.config import next_window_open

    assert next_window_open(1000) == 1800
    assert next_window_open(1800) == 2700


def test_candle_won_rules() -> None:
    def candle_won(side: str, o: float, c: float) -> bool:
        return c >= o if side == "long" else c < o

    assert candle_won("long", 100, 101) is True
    assert candle_won("long", 100, 99) is False
    assert candle_won("short", 100, 99) is True
    assert candle_won("short", 100, 101) is False


def test_live_market_usd_fixed_shares_times_ask() -> None:
    shares, cost = compute_live_market_usd(0.14, min_shares=5)
    assert shares == 5
    assert math.isclose(cost, 0.7, abs_tol=_ABS)
    shares, cost = compute_live_market_usd(0.50, min_shares=5)
    assert shares == 5
    assert math.isclose(cost, 2.5, abs_tol=_ABS)


def test_short_entry_from_yes() -> None:
    shares, cost = compute_bet(0.4, 5, 1.0)  # NO @ 0.4 when YES=0.6
    assert shares == 5
    assert cost == 2.0


def test_yes_price_from_market() -> None:
    from adapters.polymarket.gamma import yes_price_from_market

    m = {"outcomes": '["Up","Down"]', "outcomePrices": '["0.52","0.48"]'}
    assert math.isclose(yes_price_from_market(m), 0.52, abs_tol=_ABS)
