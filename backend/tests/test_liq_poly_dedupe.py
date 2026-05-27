"""Liq bar signal must not emit duplicate open_bet once reserved (runner + strategy)."""
from engines.liq_poly_runner import LiqPolyRunner
from strategies.liq_poly_config import LiqPolyRuntimeConfig, RestoreState


def _runner() -> LiqPolyRunner:
    cfg = LiqPolyRuntimeConfig(
        mode="live",
        assets={
            "DOGE": {
                "binance_symbol": "DOGEUSDT-PERP.BINANCE",
                "poly_series": "doge-updown-15m",
            }
        },
        thresholds={"DOGE": 15_000.0},
        min_usd=1.0,
        min_shares=5.0,
        orders_enabled=True,
        restore=RestoreState(),
    )
    return LiqPolyRunner(cfg)


def test_mark_signaled_blocks_second_open_on_same_liq_bar() -> None:
    runner = _runner()
    kwargs = dict(
        symbol="DOGEUSDT-PERP.BINANCE",
        bar_open=1_779_809_400,
        long_total=20_000.0,
        short_total=0.0,
        signal_ts=1_779_809_500,
    )
    first = runner.on_liq_bar(**kwargs)
    assert len(first) == 1
    assert first[0]["cmd"] == "open_bet"

    runner.mark_signaled("DOGEUSDT-PERP.BINANCE", 1_779_809_400, "long")
    second = runner.on_liq_bar(**kwargs)
    assert second == []
