"""Strategy env config tests."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.env_config import (  # noqa: E402
    build_fresh_paper_strategy_config,
    build_liquidation_signal_config,
    build_terminal_sirius_config,
)


def test_liq_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("LIQ_THRESHOLD_BTC", "123456")
    monkeypatch.setenv("LIQ_THRESHOLD_ETH", "78900")
    cfg = build_liquidation_signal_config(
        component_id="test",
        instrument_ids=("BTCUSDT-PERP.BINANCE",),
    )
    assert cfg.liq_threshold_btc == 123456.0
    assert cfg.liq_threshold_eth == 78900.0


def test_terminal_sirius_trade_size_from_env(monkeypatch):
    monkeypatch.setenv("STRATEGY_TRADE_SIZE", "25")
    cfg = build_terminal_sirius_config(
        binance_instruments=("BTCUSDT-PERP.BINANCE",),
        polymarket_series=("btc-updown-15m",),
    )
    assert str(cfg.trade_size) == "25"


def test_fresh_paper_strategy_config_from_env(monkeypatch):
    monkeypatch.setenv("PAPER_STRATEGY_ID", "new-paper")
    monkeypatch.setenv("PAPER_STRATEGY_TRADE_ENABLED", "true")
    monkeypatch.setenv("PAPER_STRATEGY_TRADE_SIZE", "15")
    cfg = build_fresh_paper_strategy_config(
        binance_instruments=("BTCUSDT-PERP.BINANCE",),
        polymarket_series=("btc-updown-15m",),
    )
    assert cfg.strategy_id == "new-paper"
    assert cfg.trade_enabled is True
    assert str(cfg.trade_size) == "15"
