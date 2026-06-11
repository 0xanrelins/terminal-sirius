from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from adapters.polymarket.rolling import WINDOW_SEC

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId

from adapters.polymarket.messages import ActivePolymarketMarket
from recorders.data_types import LiquidationTick
from strategies.config import FreshPaperStrategyConfig
from strategies.fresh_paper_strategy import FreshPaperStrategy
from strategy_signal_tags import parse_entry_signal_tag
from strategy_signal_tags import parse_exit_reason_tag

WINDOW_START = 1_780_814_700
ACTIVE_SLUG = f"btc-updown-15m-{WINDOW_START}"

YES = InstrumentId.from_str("0xyes.POLYMARKET")
NO = InstrumentId.from_str("0xno.POLYMARKET")
SYMBOL = "BTCUSDT-PERP.BINANCE"


def _strategy(*, backtest_mode: bool = False, trade_enabled: bool = False) -> FreshPaperStrategy:
    return FreshPaperStrategy(
        FreshPaperStrategyConfig(
            binance_instruments=(SYMBOL,),
            polymarket_series=("btc-updown-15m",),
            strategy_id="fresh_test",
            backtest_mode=backtest_mode,
            trade_enabled=trade_enabled,
        )
    )


def _px(precision: int, value: float = 0.40):
    px = MagicMock(precision=precision)
    px.__float__.return_value = value
    return px


def _quote(
    *,
    bid_prec: int | None = 3,
    ask_prec: int | None = 3,
    bid_value: float = 0.40,
    ask_value: float = 0.40,
):
    bid = _px(bid_prec, bid_value) if bid_prec is not None else None
    ask = _px(ask_prec, ask_value) if ask_prec is not None else None
    return MagicMock(bid_price=bid, ask_price=ask)


def _attach_clock(strategy: FreshPaperStrategy, *, timestamp_ns: int) -> MagicMock:
    clock = MagicMock()
    clock.timestamp_ns.return_value = timestamp_ns
    clock_patcher = patch.object(type(strategy), "clock", new_callable=PropertyMock, return_value=clock)
    clock_patcher.start()
    strategy._clock_patcher = clock_patcher
    return clock


def _attach_cache_and_order_factory(strategy: FreshPaperStrategy):
    cache = MagicMock()
    cache_patcher = patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=cache)
    cache_patcher.start()
    order_factory = MagicMock()
    factory_patcher = patch.object(
        type(strategy),
        "order_factory",
        new_callable=PropertyMock,
        return_value=order_factory,
    )
    factory_patcher.start()
    strategy._cache_patcher = cache_patcher
    strategy._factory_patcher = factory_patcher
    return cache, order_factory


def _mock_open_position(cache, *, position_id: PositionId, quantity: str = "filled-qty"):
    position = MagicMock(quantity=quantity, id=position_id, is_open=True, ts_closed=None)
    cache.position.return_value = position
    return position


def _active_market(strategy: FreshPaperStrategy, *, subscribe_quotes: bool = True) -> None:
    if subscribe_quotes:
        strategy.subscribe_quote_ticks = MagicMock()
    strategy._on_active_market(
        ActivePolymarketMarket(
            instrument_id=YES,
            no_instrument_id=NO,
            series="btc-updown-15m",
            slug=ACTIVE_SLUG,
            question="btc",
            ts_event=1,
            ts_init=1,
        )
    )


def _prime_entry_window(strategy: FreshPaperStrategy) -> None:
    _attach_clock(strategy, timestamp_ns=(WINDOW_START + 60) * 1_000_000_000)


def test_active_market_tracks_yes_no_and_subscribes_quotes():
    strategy = _strategy(backtest_mode=False)
    old_yes = InstrumentId.from_str("0xold-yes.POLYMARKET")
    old_no = InstrumentId.from_str("0xold-no.POLYMARKET")
    strategy._states[SYMBOL].yes_instrument_id = old_yes
    strategy._states[SYMBOL].no_instrument_id = old_no
    strategy.subscribe_quote_ticks = MagicMock()
    strategy.unsubscribe_quote_ticks = MagicMock()

    strategy._on_active_market(
        ActivePolymarketMarket(
            instrument_id=YES,
            no_instrument_id=NO,
            series="btc-updown-15m",
            slug="btc-updown-15m-1780814700",
            question="btc",
            ts_event=1,
            ts_init=1,
        )
    )

    state = strategy._states[SYMBOL]
    assert state.yes_instrument_id == YES
    assert state.no_instrument_id == NO
    strategy.subscribe_quote_ticks.assert_any_call(YES)
    strategy.subscribe_quote_ticks.assert_any_call(NO)
    strategy.unsubscribe_quote_ticks.assert_any_call(old_yes)
    strategy.unsubscribe_quote_ticks.assert_any_call(old_no)


def test_entry_tags_use_strategy_agnostic_format():
    strategy = _strategy()

    tags = strategy._entry_tags(
        symbol=SYMBOL,
        direction="LONG",
        reason="rule_probe",
        context={"source": "test"},
    )

    parsed = parse_entry_signal_tag(tags)
    assert parsed is not None
    assert parsed["strategy_id"] == "fresh_test"
    assert parsed["symbol"] == SYMBOL
    assert parsed["direction"] == "LONG"
    assert parsed["source"] == "test"


def test_trade_disabled_evaluate_does_not_submit_order():
    strategy = _strategy(trade_enabled=False)
    strategy.submit_order = MagicMock()

    strategy._evaluate_symbol(SYMBOL)

    strategy.submit_order.assert_not_called()


def test_liquidation_tick_submits_long_recovery_limit_order():
    strategy = _strategy(trade_enabled=True)
    _prime_entry_window(strategy)
    _active_market(strategy)
    cache, order_factory = _attach_cache_and_order_factory(strategy)
    inst = MagicMock()
    inst.price_precision = 3
    inst.make_qty.return_value = "10-share"
    inst.make_price.return_value = "0.50-price"
    cache.instrument.return_value = inst
    cache.quote_tick.return_value = _quote()
    order = MagicMock(client_order_id="ENTRY-1")
    order_factory.limit.return_value = order
    strategy.submit_order = MagicMock()

    strategy._on_liquidation_tick(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=10_000.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )

    order_factory.limit.assert_called_once()
    kwargs = order_factory.limit.call_args.kwargs
    assert kwargs["instrument_id"] == YES
    assert kwargs["order_side"] == OrderSide.BUY
    assert kwargs["quantity"] == "10-share"
    assert kwargs["price"] == "0.50-price"
    parsed = parse_entry_signal_tag(kwargs["tags"])
    assert parsed is not None
    assert parsed["direction"] == "LONG"
    assert parsed["anchor"] == "100.0"
    strategy.submit_order.assert_called_once_with(order)


def test_liquidation_tick_below_threshold_does_not_submit():
    strategy = _strategy(trade_enabled=True)
    strategy.submit_order = MagicMock()

    strategy._on_liquidation_tick(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=9_999.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )

    strategy.submit_order.assert_not_called()


def test_trade_tick_recovery_exit_submits_market_sell():
    strategy = _strategy(trade_enabled=True)
    _prime_entry_window(strategy)
    _active_market(strategy)
    cache, order_factory = _attach_cache_and_order_factory(strategy)
    inst = MagicMock()
    inst.price_precision = 3
    inst.make_qty.return_value = "10-share"
    inst.make_price.return_value = "0.50-price"
    cache.instrument.return_value = inst
    cache.quote_tick.return_value = _quote()
    position_id = PositionId("P-1")
    _mock_open_position(cache, position_id=position_id)
    entry_order = MagicMock(client_order_id="ENTRY-1")
    exit_order = MagicMock(client_order_id="EXIT-1")
    order_factory.limit.return_value = entry_order
    order_factory.market.return_value = exit_order
    strategy.submit_order = MagicMock()

    strategy._on_liquidation_tick(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=10_000.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )
    strategy.on_order_filled(
        MagicMock(
            client_order_id="ENTRY-1",
            order_side=OrderSide.BUY,
            instrument_id=YES,
            last_qty="10-share",
            last_px="0.50-price",
            position_id=position_id,
        )
    )

    strategy.on_trade_tick(MagicMock(instrument_id=InstrumentId.from_str(SYMBOL), price=_px(2, 100.2)))

    order_factory.market.assert_called_once()
    kwargs = order_factory.market.call_args.kwargs
    assert kwargs["instrument_id"] == YES
    assert kwargs["order_side"] == OrderSide.SELL
    assert kwargs["quantity"] == "filled-qty"
    assert kwargs["reduce_only"] is True
    strategy.submit_order.assert_any_call(exit_order, position_id=position_id)


def test_entry_blocked_when_window_closes_within_200_seconds():
    strategy = _strategy(trade_enabled=True, backtest_mode=True)
    remaining = 150
    _attach_clock(strategy, timestamp_ns=(WINDOW_START + WINDOW_SEC - remaining) * 1_000_000_000)
    _active_market(strategy)
    cache, order_factory = _attach_cache_and_order_factory(strategy)
    inst = MagicMock()
    inst.price_precision = 3
    inst.make_qty.return_value = "10-share"
    inst.make_price.return_value = "0.50-price"
    cache.instrument.return_value = inst
    cache.quote_tick.return_value = _quote()
    order_factory.limit.return_value = MagicMock(client_order_id="ENTRY-1")
    strategy.submit_order = MagicMock()

    strategy._on_liquidation_tick(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=10_000.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )

    strategy.submit_order.assert_not_called()


def test_liquidation_exit_submits_market_sell_on_adverse_move():
    strategy = _strategy(trade_enabled=True)
    _prime_entry_window(strategy)
    _active_market(strategy)
    cache, order_factory = _attach_cache_and_order_factory(strategy)
    inst = MagicMock()
    inst.price_precision = 3
    inst.make_qty.return_value = "10-share"
    inst.make_price.return_value = "0.50-price"
    cache.instrument.return_value = inst
    cache.quote_tick.return_value = _quote()
    position_id = PositionId("P-1")
    _mock_open_position(cache, position_id=position_id)
    entry_order = MagicMock(client_order_id="ENTRY-1")
    exit_order = MagicMock(client_order_id="EXIT-1")
    order_factory.limit.return_value = entry_order
    order_factory.market.return_value = exit_order
    strategy.submit_order = MagicMock()

    strategy._on_liquidation_tick(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=10_000.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )
    strategy.on_order_filled(
        MagicMock(
            client_order_id="ENTRY-1",
            order_side=OrderSide.BUY,
            instrument_id=YES,
            last_qty="10-share",
            last_px="0.50-price",
            position_id=position_id,
        )
    )

    strategy.on_trade_tick(MagicMock(instrument_id=InstrumentId.from_str(SYMBOL), price=_px(2, 99.8)))

    order_factory.market.assert_called_once()
    assert parse_exit_reason_tag(order_factory.market.call_args.kwargs["tags"]) == "liquidation_exit_0p2"


def test_time_stop_submits_market_sell_after_200_seconds():
    strategy = _strategy(trade_enabled=True)
    entry_ts_ns = (WINDOW_START + 60) * 1_000_000_000
    _attach_clock(strategy, timestamp_ns=entry_ts_ns + 201 * 1_000_000_000)
    _active_market(strategy)
    cache, order_factory = _attach_cache_and_order_factory(strategy)
    inst = MagicMock()
    inst.price_precision = 3
    inst.make_qty.return_value = "10-share"
    inst.make_price.return_value = "0.50-price"
    cache.instrument.return_value = inst
    cache.quote_tick.return_value = _quote()
    position_id = PositionId("P-1")
    _mock_open_position(cache, position_id=position_id)
    entry_order = MagicMock(client_order_id="ENTRY-1")
    exit_order = MagicMock(client_order_id="EXIT-1")
    order_factory.limit.return_value = entry_order
    order_factory.market.return_value = exit_order
    strategy.submit_order = MagicMock()

    strategy._on_liquidation_tick(
        LiquidationTick(
            symbol=SYMBOL,
            side="SELL",
            notional=10_000.0,
            price=100.0,
            quantity=1.0,
            order_id=1,
            ts_event=1,
            ts_init=1,
        )
    )
    strategy.on_order_filled(
        MagicMock(
            client_order_id="ENTRY-1",
            order_side=OrderSide.BUY,
            instrument_id=YES,
            last_qty="10-share",
            last_px="0.50-price",
            position_id=position_id,
        )
    )
    strategy._recoveries[0].entry_ts_ns = entry_ts_ns

    strategy.on_trade_tick(MagicMock(instrument_id=InstrumentId.from_str(SYMBOL), price=_px(2, 100.05)))

    order_factory.market.assert_called_once()
    assert parse_exit_reason_tag(order_factory.market.call_args.kwargs["tags"]) == "time_stop_200s"


def test_liquidation_exit_closes_only_matching_position():
    strategy = _strategy(trade_enabled=True)
    _prime_entry_window(strategy)
    _active_market(strategy)
    cache, order_factory = _attach_cache_and_order_factory(strategy)
    inst = MagicMock()
    inst.price_precision = 3
    inst.make_qty.return_value = "10-share"
    inst.make_price.return_value = "0.50-price"
    cache.instrument.return_value = inst
    cache.quote_tick.return_value = _quote()
    position_a = PositionId("P-1")
    position_b = PositionId("P-2")
    qty_a = "10-share"
    qty_b = "20-share"
    positions = {
        position_a: MagicMock(quantity=qty_a, id=position_a, is_open=True, ts_closed=None),
        position_b: MagicMock(quantity=qty_b, id=position_b, is_open=True, ts_closed=None),
    }
    cache.position.side_effect = lambda pid: positions.get(pid)
    entry_a = MagicMock(client_order_id="ENTRY-1")
    entry_b = MagicMock(client_order_id="ENTRY-2")
    exit_order = MagicMock(client_order_id="EXIT-1")
    order_factory.limit.side_effect = [entry_a, entry_b]
    order_factory.market.return_value = exit_order
    strategy.submit_order = MagicMock()

    for order_id, position_id, anchor in (
        ("ENTRY-1", position_a, 100.0),
        ("ENTRY-2", position_b, 99.9),
    ):
        strategy._on_liquidation_tick(
            LiquidationTick(
                symbol=SYMBOL,
                side="SELL",
                notional=10_000.0,
                price=anchor,
                quantity=1.0,
                order_id=1,
                ts_event=1,
                ts_init=1,
            )
        )
        strategy.on_order_filled(
            MagicMock(
                client_order_id=order_id,
                order_side=OrderSide.BUY,
                instrument_id=YES,
                last_qty="10-share",
                last_px="0.50-price",
                position_id=position_id,
            )
        )

    strategy.on_trade_tick(MagicMock(instrument_id=InstrumentId.from_str(SYMBOL), price=_px(2, 99.8)))

    order_factory.market.assert_called_once()
    kwargs = order_factory.market.call_args.kwargs
    assert kwargs["quantity"] == qty_a
    strategy.submit_order.assert_called_with(exit_order, position_id=position_a)
    assert len(strategy._recoveries) == 2
    assert strategy._recoveries[0].exit_submitted is True
    assert strategy._recoveries[1].exit_submitted is False


def test_on_position_closed_removes_only_matching_recovery():
    strategy = _strategy(trade_enabled=True)
    from strategies.fresh_paper_strategy import _RecoveryTrade

    position_a = PositionId("P-1")
    position_b = PositionId("P-2")
    strategy._recoveries = [
        _RecoveryTrade(
            symbol=SYMBOL,
            direction="LONG",
            instrument_id=YES,
            anchor_price=100.0,
            entry_order_id="ENTRY-1",
            position_id=position_a,
            active=True,
            exit_submitted=True,
        ),
        _RecoveryTrade(
            symbol=SYMBOL,
            direction="LONG",
            instrument_id=YES,
            anchor_price=99.9,
            entry_order_id="ENTRY-2",
            position_id=position_b,
            active=True,
            exit_submitted=False,
        ),
    ]
    strategy._recoveries_by_entry_order = {
        "ENTRY-1": strategy._recoveries[0],
        "ENTRY-2": strategy._recoveries[1],
    }

    strategy.on_position_closed(MagicMock(position_id=position_a, instrument_id=YES))

    assert len(strategy._recoveries) == 1
    assert strategy._recoveries[0].position_id == position_b
