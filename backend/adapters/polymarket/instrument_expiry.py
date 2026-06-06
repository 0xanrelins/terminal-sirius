"""Align Polymarket 15m BinaryOption expiration with slug window end (native sandbox path)."""
from __future__ import annotations

from nautilus_trader.model.instruments import BinaryOption

from adapters.polymarket.rolling import WINDOW_SEC
from adapters.polymarket.rolling import parse_window_epoch_from_slug

# Sandbox expiration fires after settlement actor has the Binance 15m bar.
DEFAULT_EXPIRY_GRACE_SEC = 10.0


def expiration_ns_for_slug(slug: str, *, grace_sec: float = DEFAULT_EXPIRY_GRACE_SEC) -> int | None:
    """``expiration_ns`` at window end + grace so ``InstrumentClose`` can arrive first."""
    window_start_sec = parse_window_epoch_from_slug(slug)
    if window_start_sec is None:
        return None
    window_end_sec = window_start_sec + WINDOW_SEC
    return int((window_end_sec + grace_sec) * 1_000_000_000)


def align_binary_option_expiration(
    instrument: BinaryOption,
    slug: str,
    *,
    grace_sec: float = DEFAULT_EXPIRY_GRACE_SEC,
) -> BinaryOption:
    """Return instrument with slug-aligned ``expiration_ns`` (or unchanged if not a 15m slug)."""
    expiration_ns = expiration_ns_for_slug(slug, grace_sec=grace_sec)
    if expiration_ns is None or instrument.expiration_ns == expiration_ns:
        return instrument
    return BinaryOption(
        instrument_id=instrument.id,
        raw_symbol=instrument.raw_symbol,
        asset_class=instrument.asset_class,
        currency=instrument.quote_currency,
        price_precision=instrument.price_precision,
        size_precision=instrument.size_precision,
        price_increment=instrument.price_increment,
        size_increment=instrument.size_increment,
        activation_ns=instrument.activation_ns,
        expiration_ns=expiration_ns,
        ts_event=instrument.ts_event,
        ts_init=instrument.ts_init,
        max_quantity=instrument.max_quantity,
        min_quantity=instrument.min_quantity,
        maker_fee=instrument.maker_fee,
        taker_fee=instrument.taker_fee,
        outcome=instrument.outcome,
        description=instrument.description,
        tick_scheme_name=instrument.tick_scheme_name,
        info=instrument.info,
    )
