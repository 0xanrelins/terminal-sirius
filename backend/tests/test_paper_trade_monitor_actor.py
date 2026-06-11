from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from nautilus_trader.model.identifiers import PositionId

from paper_trade_monitor_actor import PaperTradeMonitorActor
from paper_trade_monitor_actor import PaperTradeMonitorActorConfig
from strategy_signal_tags import build_paper_exit_reason_tags


def _actor() -> PaperTradeMonitorActor:
    actor = PaperTradeMonitorActor(
        PaperTradeMonitorActorConfig(strategy_id="fresh_test"),
        MagicMock(),
    )
    actor._started_ns = 1_000
    return actor


def _attach_cache(actor: PaperTradeMonitorActor, cache: MagicMock) -> None:
    patcher = patch.object(type(actor), "cache", new_callable=PropertyMock, return_value=cache)
    patcher.start()
    actor._cache_patcher = patcher


def test_close_reason_uses_orders_for_position():
    actor = _actor()
    cache = MagicMock()
    position_id = PositionId("P-ETH-1")
    sell = MagicMock()
    sell.side.name = "SELL"
    sell.tags = build_paper_exit_reason_tags(
        strategy_id="fresh_test",
        reason="liquidation_exit_0p2",
        symbol="ETHUSDT-PERP.BINANCE",
        direction="LONG",
    )
    cache.orders_for_position.return_value = [sell]
    _attach_cache(actor, cache)

    assert actor._close_reason_for_position(position_id) == "liquidation_exit_0p2"
    cache.orders_for_position.assert_called_once_with(position_id)


def test_positions_closed_this_run_filters_by_started_ns():
    actor = _actor()
    cache = MagicMock()
    old = MagicMock(ts_closed=500)
    current = MagicMock(ts_closed=2_000)
    cache.positions_closed.return_value = [old, current]
    _attach_cache(actor, cache)

    assert actor._positions_closed_this_run() == [current]
