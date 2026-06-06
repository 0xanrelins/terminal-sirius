"""Shared Terminal Sirius signal derivation (strategy + UI bridge)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EntryDirection = Literal["LONG", "SHORT", "HOLD"]


@dataclass(frozen=True)
class SignalInputs:
    vwap: float | None
    slope: float | None
    low_zone: float | None
    high_zone: float | None
    last_price: float | None
    liq_long_trigger: bool
    liq_short_trigger: bool
    slope_eps: float
    vwap_ready: bool


@dataclass(frozen=True)
class SignalDerived:
    in_range: bool
    long_zone: bool
    short_zone: bool
    decision: EntryDirection


def entry_direction(inputs: SignalInputs) -> str | None:
    """Return LONG/SHORT when entry conditions match; None otherwise."""
    slope = inputs.slope
    price = inputs.last_price
    if slope is None or price is None or inputs.low_zone is None or inputs.high_zone is None:
        return None
    in_range = abs(slope) <= inputs.slope_eps
    long_zone = price < inputs.low_zone
    short_zone = price > inputs.high_zone

    if (slope > inputs.slope_eps or in_range) and long_zone and inputs.liq_long_trigger:
        return "LONG"
    if (slope < -inputs.slope_eps or in_range) and short_zone and inputs.liq_short_trigger:
        return "SHORT"
    return None


def compute_derived(inputs: SignalInputs) -> SignalDerived:
    """Zone flags + entry decision for UI and monitoring."""
    slope = inputs.slope
    price = inputs.last_price
    in_range = slope is not None and abs(slope) <= inputs.slope_eps
    long_zone = (
        price is not None and inputs.low_zone is not None and price < inputs.low_zone
    )
    short_zone = (
        price is not None and inputs.high_zone is not None and price > inputs.high_zone
    )

    if not inputs.vwap_ready or slope is None or inputs.low_zone is None:
        return SignalDerived(
            in_range=in_range,
            long_zone=long_zone,
            short_zone=short_zone,
            decision="HOLD",
        )

    direction = entry_direction(inputs)
    if direction is None:
        return SignalDerived(
            in_range=in_range,
            long_zone=long_zone,
            short_zone=short_zone,
            decision="HOLD",
        )
    return SignalDerived(
        in_range=in_range,
        long_zone=long_zone,
        short_zone=short_zone,
        decision=direction,
    )
