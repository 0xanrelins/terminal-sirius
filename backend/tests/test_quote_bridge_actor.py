"""Tests for Polymarket quote bridge UI broadcast filtering."""
from unittest.mock import MagicMock
from unittest.mock import PropertyMock
from unittest.mock import patch

from nautilus_trader.model.identifiers import InstrumentId

from adapters.polymarket.quote_bridge_actor import PolymarketQuoteBridgeActor
from adapters.polymarket.quote_bridge_actor import PolymarketQuoteBridgeActorConfig
from adapters.polymarket.quote_bridge_actor import should_broadcast_quote


def test_broadcast_yes_token_direct_slug() -> None:
    meta = {
        "slug": "btc-updown-15m-123",
        "token": "yes",
        "series": None,
    }
    assert should_broadcast_quote(meta, {}) is True


def test_skip_no_token() -> None:
    meta = {
        "slug": "btc-updown-15m-123",
        "token": "no",
        "series": None,
    }
    assert should_broadcast_quote(meta, {}) is False


def test_broadcast_current_series_slug() -> None:
    series = "btc-updown-15m"
    current = f"{series}-123"
    meta = {
        "slug": current,
        "token": "yes",
        "series": series,
    }
    assert should_broadcast_quote(meta, {series: current}) is True


def test_skip_next_window_prefetch_slug() -> None:
    series = "btc-updown-15m"
    current = f"{series}-123"
    nxt = f"{series}-456"
    meta = {
        "slug": nxt,
        "token": "yes",
        "series": series,
    }
    assert should_broadcast_quote(meta, {series: current}) is False


def test_skip_no_token_even_for_current_series_slug() -> None:
    series = "btc-updown-15m"
    current = f"{series}-123"
    meta = {
        "slug": current,
        "token": "no",
        "series": series,
    }
    assert should_broadcast_quote(meta, {series: current}) is False


def test_publish_active_market_skips_if_no_outcome_pair() -> None:
    actor = PolymarketQuoteBridgeActor(
        PolymarketQuoteBridgeActorConfig(),
        data_queue=MagicMock(),
    )
    yes = InstrumentId.from_str("0xyes.POLYMARKET")
    actor._slug_quote_iid["slug-1"] = yes
    actor._slug_to_iids["slug-1"] = [yes]
    actor.publish_data = MagicMock()

    clock = MagicMock()
    with patch.object(type(actor), "clock", new_callable=PropertyMock, return_value=clock):
        actor._publish_active_market("btc-updown-15m", "slug-1")

    actor.publish_data.assert_not_called()
