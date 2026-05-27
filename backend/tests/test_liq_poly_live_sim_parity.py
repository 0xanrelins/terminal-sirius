"""Live and sim modes use the same LiqPolyRunner signal rules (only mode label differs)."""
from engines.liq_poly_runner import LiqPolyRunner
from strategies.liq_poly_config import LiqPolyRuntimeConfig, RestoreState

_ASSETS = {
    "DOGE": {
        "binance_symbol": "DOGEUSDT-PERP.BINANCE",
        "poly_series": "doge-updown-15m",
    }
}
_THRESHOLDS = {"DOGE": 200_000.0}
_BAR = dict(
    symbol="DOGEUSDT-PERP.BINANCE",
    bar_open=1_779_809_400,
    long_total=250_000.0,
    short_total=0.0,
    signal_ts=1_779_809_500,
)


def _runner(mode: str) -> LiqPolyRunner:
    return LiqPolyRunner(
        LiqPolyRuntimeConfig(
            mode=mode,  # type: ignore[arg-type]
            assets=_ASSETS,
            thresholds=_THRESHOLDS,
            min_usd=1.0,
            min_shares=5.0,
            orders_enabled=True,
            restore=RestoreState(),
        )
    )


def test_live_and_sim_emit_same_open_bet_on_identical_liq_bar() -> None:
    live_cmds = _runner("live").on_liq_bar(**_BAR)
    sim_cmds = _runner("sim").on_liq_bar(**_BAR)
    assert len(live_cmds) == len(sim_cmds) == 1
    live_open = live_cmds[0]
    sim_open = sim_cmds[0]
    assert live_open["cmd"] == sim_open["cmd"] == "open_bet"
    assert live_open["mode"] == "live"
    assert sim_open["mode"] == "sim"
    for key in (
        "asset",
        "side",
        "leg",
        "threshold",
        "liq_bar_open",
        "candle_open",
        "binance_symbol",
    ):
        assert live_open[key] == sim_open[key]
