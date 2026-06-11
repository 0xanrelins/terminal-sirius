"""Entry guard: Polymarket window, instrument readiness, and quote checks."""

from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.messages import ActivePolymarketMarket
from strategies.terminal_sirius_strategy import Decision
from strategies.terminal_sirius_strategy import TerminalSiriusStrategy
from strategies.terminal_sirius_strategy import TerminalSiriusStrategyConfig

YES = InstrumentId.from_str("0xyes.POLYMARKET")
NO = InstrumentId.from_str("0xno.POLYMARKET")
SYMBOL = "BTCUSDT-PERP.BINANCE"


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


def _strategy(clock: MagicMock, *, backtest_mode: bool = True) -> TerminalSiriusStrategy:
    cfg = TerminalSiriusStrategyConfig(
        binance_instruments=("BTCUSDT-PERP.BINANCE",),
        polymarket_series=("btc-updown-15m",),
        backtest_mode=backtest_mode,
    )
    s = TerminalSiriusStrategy(cfg)
    clock_patcher = patch.object(type(s), "clock", new_callable=PropertyMock, return_value=clock)
    clock_patcher.start()
    cache = MagicMock()
    cache_patcher = patch.object(type(s), "cache", new_callable=PropertyMock, return_value=cache)
    cache_patcher.start()
    s._clock_patcher = clock_patcher
    s._cache_patcher = cache_patcher
    s._cache_mock = cache
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


def _prime_open_state(s: TerminalSiriusStrategy, clock: MagicMock) -> None:
    window_start = 1_780_814_700
    clock.timestamp_ns.return_value = (window_start + 60) * 1_000_000_000
    s._cache_mock.positions_open.return_value = False
    st = s._states[SYMBOL]
    st.vwap_ready = True
    st.slope = 0.0
    st.low_zone = 90_000.0
    st.high_zone = 91_000.0
    st.last_price = 89_500.0
    st.liq_long_trigger = True
    s._poly_iid[SYMBOL] = YES
    s._poly_no_iid[SYMBOL] = NO


def test_maybe_execute_skips_without_instrument_in_cache():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    s._cache_mock.instrument.return_value = None
    s.submit_order = MagicMock()

    s._maybe_execute(SYMBOL, Decision.OPEN)

    s.submit_order.assert_not_called()


def test_maybe_execute_skips_without_usable_quote():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    inst = MagicMock()
    inst.expiration_ns = (1_780_814_700 + 900 + 10) * 1_000_000_000
    inst.price_precision = 3
    inst.info = {"market_slug": "btc-updown-15m-1780814700"}
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = None
    s.submit_order = MagicMock()

    s._maybe_execute(SYMBOL, Decision.OPEN)

    s.submit_order.assert_not_called()


def test_maybe_execute_submits_when_ready():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    inst = MagicMock()
    inst.expiration_ns = (1_780_814_700 + 900 + 10) * 1_000_000_000
    inst.info = {"market_slug": "btc-updown-15m-1780814700"}
    inst.price_precision = 3
    inst.make_qty.return_value = MagicMock()
    inst.make_price.return_value = MagicMock()
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote()
    order_factory = MagicMock()
    order_factory.limit.return_value = MagicMock()
    patch.object(
        type(s), "order_factory", new_callable=PropertyMock, return_value=order_factory
    ).start()
    s.submit_order = MagicMock()

    s._maybe_execute(SYMBOL, Decision.OPEN)

    s.submit_order.assert_called_once()


def test_recalculate_checks_exit_before_vwap_ready_gate():
    clock = MagicMock()
    s = _strategy(clock)
    st = s._states[SYMBOL]
    st.vwap_ready = False
    st.slope = None
    st.low_zone = None
    st.last_price = 90_180.0
    s._poly_iid[SYMBOL] = YES
    s._held_instrument_id[SYMBOL] = YES
    s._entry_anchor_price[SYMBOL] = 90_000.0
    s._entry_side[SYMBOL] = "LONG"
    s._cache_mock.positions_open.return_value = True

    assert s._recalculate(SYMBOL) == Decision.CLOSE


def test_exit_decision_long_hits_recovery_threshold():
    clock = MagicMock()
    s = _strategy(clock)
    st = s._states[SYMBOL]
    st.last_price = 90_200.0
    s._entry_anchor_price[SYMBOL] = 90_000.0
    s._entry_side[SYMBOL] = "LONG"

    assert s._exit_decision(SYMBOL, st, YES) == Decision.CLOSE


def test_exit_uses_held_instrument_after_poly_rotation():
    clock = MagicMock()
    s = _strategy(clock)
    old_yes = InstrumentId.from_str("0xold-yes.POLYMARKET")
    new_yes = InstrumentId.from_str("0xnew-yes.POLYMARKET")
    s._poly_iid[SYMBOL] = new_yes
    s._held_instrument_id[SYMBOL] = old_yes
    s._entry_anchor_price[SYMBOL] = 90_000.0
    s._entry_side[SYMBOL] = "LONG"
    st = s._states[SYMBOL]
    st.last_price = 90_200.0

    position = MagicMock(quantity=MagicMock())

    def positions_open(*, instrument_id=None, venue=None):
        if instrument_id == old_yes:
            return [position]
        return []

    s._cache_mock.positions_open.side_effect = positions_open
    inst = MagicMock()
    inst.price_precision = 3
    inst.expiration_ns = 1_000_000_000_000_000
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote()
    order_factory = MagicMock()
    order_factory.market.return_value = MagicMock()
    patch.object(
        type(s), "order_factory", new_callable=PropertyMock, return_value=order_factory
    ).start()
    s.submit_order = MagicMock()

    s._maybe_execute(SYMBOL, Decision.CLOSE)

    assert order_factory.market.call_args.kwargs["instrument_id"] == old_yes
    s.submit_order.assert_called_once()


def test_submit_market_exit_preserves_close_reason_tag():
    clock = MagicMock()
    s = _strategy(clock)
    inst = MagicMock()
    inst.price_precision = 3
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote()
    s._cache_mock.positions_open.return_value = [MagicMock(quantity=MagicMock())]
    order_factory = MagicMock()
    order_factory.market.return_value = MagicMock()
    patch.object(
        type(s), "order_factory", new_callable=PropertyMock, return_value=order_factory
    ).start()
    s.submit_order = MagicMock()

    assert s._submit_market_exit(SYMBOL, YES, reason="recovery_exit_0p2") is True

    assert order_factory.market.call_args.kwargs["tags"] == [
        "ts-exit:reason=recovery_exit_0p2"
    ]
    s.submit_order.assert_called_once()


def test_maybe_execute_skips_on_stale_quote_precision():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    inst = MagicMock()
    inst.price_precision = 3
    inst.expiration_ns = (1_780_814_700 + 900 + 10) * 1_000_000_000
    inst.info = {"market_slug": "btc-updown-15m-1780814700"}
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote(bid_prec=2, ask_prec=2)
    s.submit_order = MagicMock()

    s._maybe_execute(SYMBOL, Decision.OPEN)

    s.submit_order.assert_not_called()


def test_maybe_execute_skips_when_token_mid_above_max_entry_price():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    inst = MagicMock()
    inst.price_precision = 3
    inst.expiration_ns = (1_780_814_700 + 900 + 10) * 1_000_000_000
    inst.info = {"market_slug": "btc-updown-15m-1780814700"}
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote(
        bid_prec=3,
        ask_prec=3,
        bid_value=0.40,
        ask_value=0.60,
    )
    s.submit_order = MagicMock()

    s._maybe_execute(SYMBOL, Decision.OPEN)

    s.submit_order.assert_not_called()


def test_on_active_market_subscribes_quotes_on_rotate():
    clock = MagicMock()
    s = _strategy(clock, backtest_mode=False)
    old_yes = InstrumentId.from_str("0xold-yes.POLYMARKET")
    old_no = InstrumentId.from_str("0xold-no.POLYMARKET")
    s._poly_iid[SYMBOL] = old_yes
    s._poly_no_iid[SYMBOL] = old_no
    s.subscribe_quote_ticks = MagicMock()
    s.unsubscribe_quote_ticks = MagicMock()

    data = ActivePolymarketMarket(
        instrument_id=YES,
        no_instrument_id=NO,
        series="btc-updown-15m",
        slug="btc-updown-15m-1780814700",
        question="btc",
        ts_event=1,
        ts_init=1,
    )
    s._on_active_market(data)

    assert s._poly_iid[SYMBOL] == YES
    assert s._poly_no_iid[SYMBOL] == NO
    assert s.subscribe_quote_ticks.call_count == 2
    s.unsubscribe_quote_ticks.assert_any_call(old_yes)
    s.unsubscribe_quote_ticks.assert_any_call(old_no)


def test_on_instrument_retries_open_when_cache_ready():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    s._poly_iid[SYMBOL] = YES
    inst = MagicMock()
    inst.id = YES
    inst.price_precision = 3
    inst.expiration_ns = (1_780_814_700 + 900 + 10) * 1_000_000_000
    inst.info = {"market_slug": "btc-updown-15m-1780814700"}
    inst.make_qty.return_value = MagicMock()
    inst.make_price.return_value = MagicMock()
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote()
    order_factory = MagicMock()
    order_factory.limit.return_value = MagicMock()
    patch.object(
        type(s), "order_factory", new_callable=PropertyMock, return_value=order_factory
    ).start()
    s.submit_order = MagicMock()

    s.on_instrument(inst)

    s.submit_order.assert_called_once()


def test_on_instrument_skips_submit_until_quote_precision_matches():
    clock = MagicMock()
    s = _strategy(clock)
    _prime_open_state(s, clock)
    s._poly_iid[SYMBOL] = YES
    inst = MagicMock()
    inst.id = YES
    inst.price_precision = 3
    inst.expiration_ns = (1_780_814_700 + 900 + 10) * 1_000_000_000
    inst.info = {"market_slug": "btc-updown-15m-1780814700"}
    s._cache_mock.instrument.return_value = inst
    s._cache_mock.quote_tick.return_value = _quote(bid_prec=2, ask_prec=2)
    s.submit_order = MagicMock()

    s.on_instrument(inst)

    s.submit_order.assert_not_called()
