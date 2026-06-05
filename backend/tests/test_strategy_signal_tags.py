from strategy_signal_tags import (
    build_entry_signal_tags,
    entry_signal_from_order_tags,
    parse_entry_signal_tag,
)


def test_roundtrip_tag():
    tags = build_entry_signal_tags(
        symbol="BTCUSDT",
        direction="LONG",
        vwap=0.23,
        slope=0.0123,
        low_zone=0.2,
        high_zone=0.28,
        last_price=0.22,
        liq_long=True,
        liq_short=False,
    )
    assert len(tags) == 1
    assert tags[0].startswith("ts-sig:")
    parsed = parse_entry_signal_tag(tags)
    assert parsed is not None
    assert parsed["sym"] == "BTCUSDT"
    assert parsed["dir"] == "LONG"
    assert parsed["ll"] == "1"
    assert parsed["ls"] == "0"
    display, tip = entry_signal_from_order_tags(tags)
    assert "liqL" in display
    assert "sym=BTCUSDT" in tip
