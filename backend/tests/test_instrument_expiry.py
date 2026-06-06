"""Polymarket 15m instrument expiration alignment."""

from unittest.mock import MagicMock

from adapters.polymarket.instrument_expiry import expiration_ns_for_slug
from adapters.polymarket.rolling import WINDOW_SEC


def test_expiration_ns_for_slug_window_end_plus_grace():
    slug = "btc-updown-15m-1780714800"
    grace = 10.0
    expected = int((1780714800 + WINDOW_SEC + grace) * 1_000_000_000)
    assert expiration_ns_for_slug(slug, grace_sec=grace) == expected


def test_expiration_ns_for_non_rolling_slug():
    assert expiration_ns_for_slug("some-static-market") is None


def test_align_binary_option_expiration_updates_ns():
    from adapters.polymarket.instrument_expiry import align_binary_option_expiration

    inst = MagicMock()
    inst.expiration_ns = 1
    inst.id = "id"
    inst.raw_symbol = "sym"
    inst.asset_class = "ALT"
    inst.quote_currency = "USD"
    inst.price_precision = 2
    inst.size_precision = 6
    inst.price_increment = MagicMock()
    inst.size_increment = MagicMock()
    inst.activation_ns = 0
    inst.ts_event = 0
    inst.ts_init = 0
    inst.max_quantity = None
    inst.min_quantity = None
    inst.maker_fee = 0
    inst.taker_fee = 0
    inst.outcome = "YES"
    inst.description = "q"
    inst.tick_scheme_name = None
    inst.info = {}

    slug = "btc-updown-15m-1780714800"
    target = expiration_ns_for_slug(slug)
    assert target is not None

    # Patch BinaryOption constructor for unit test
    import adapters.polymarket.instrument_expiry as mod

    captured: dict = {}

    class FakeBinaryOption:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    original = mod.BinaryOption
    mod.BinaryOption = FakeBinaryOption
    try:
        align_binary_option_expiration(inst, slug)
    finally:
        mod.BinaryOption = original

    assert captured["expiration_ns"] == target
