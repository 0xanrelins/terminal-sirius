from strategy_signal_tags import (
    build_entry_signal_tags,
    build_exit_reason_tags,
    build_paper_entry_signal_tags,
    build_paper_exit_reason_tags,
    entry_signal_from_order_tags,
    format_recovery_exit_label,
    parse_exit_reason_tag,
    parse_entry_signal_tag,
    recovery_exit_pct_from_reason,
    recovery_exit_reason,
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


def test_exit_reason_roundtrip():
    tags = build_exit_reason_tags(reason="recovery_exit_0p2")
    assert tags == ["ts-exit:reason=recovery_exit_0p2"]
    assert parse_exit_reason_tag(tags) == "recovery_exit_0p2"


def test_recovery_exit_reason_tracks_config_pct():
    assert recovery_exit_reason(0.2) == "recovery_exit_0p2"
    assert recovery_exit_reason(0.25) == "recovery_exit_0p25"
    assert recovery_exit_reason(1.0) == "recovery_exit_1"
    assert recovery_exit_pct_from_reason("recovery_exit_0p5") == 0.5
    assert format_recovery_exit_label(0.2) == "REC 0.2%"


def test_paper_entry_tag_normalizes_strategy_fields():
    tags = build_paper_entry_signal_tags(
        strategy_id="fresh_paper",
        symbol="BTCUSDT-PERP.BINANCE",
        direction="LONG",
        reason="first_rule",
        context={"vwap": 100.25},
    )
    assert tags == [
        "paper-sig:strategy_id=fresh_paper;symbol=BTCUSDT-PERP.BINANCE;"
        "direction=LONG;reason=first_rule;vwap=100.25"
    ]
    parsed = parse_entry_signal_tag(tags)
    assert parsed is not None
    assert parsed["strategy_id"] == "fresh_paper"
    assert parsed["symbol"] == "BTCUSDT-PERP.BINANCE"
    assert parsed["sym"] == "BTCUSDT-PERP.BINANCE"
    assert parsed["direction"] == "LONG"
    assert parsed["dir"] == "LONG"

    display, tip = entry_signal_from_order_tags(tags)
    assert display == "fresh_paper · LONG · first_rule"
    assert "strategy_id=fresh_paper" in tip


def test_paper_exit_reason_roundtrip():
    tags = build_paper_exit_reason_tags(
        strategy_id="fresh_paper",
        reason="rule_exit",
        symbol="BTCUSDT-PERP.BINANCE",
        direction="LONG",
    )
    assert parse_exit_reason_tag(tags) == "rule_exit"
