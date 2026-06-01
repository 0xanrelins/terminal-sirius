"""Rolling slug helpers: bet window vs Polymarket DataClient subscribe set."""
from adapters.polymarket.rolling import (
    WINDOW_SEC,
    active_rolling_slugs,
    bet_window_slug,
    slug_for_series,
)


def test_bet_window_slug_is_liq_bar_plus_window() -> None:
    liq = 1_779_853_500
    assert bet_window_slug("btc-updown-15m", liq) == f"btc-updown-15m-{liq + WINDOW_SEC}"


def test_active_rolling_slugs_includes_bet_target_for_current_bar() -> None:
    ts = 1_779_855_200
    current, nxt = active_rolling_slugs("btc-updown-15m", ts=ts)
    assert current == slug_for_series("btc-updown-15m", ts=ts)
    bar_open = int(current.rsplit("-", 1)[-1])
    assert nxt == bet_window_slug("btc-updown-15m", bar_open)
