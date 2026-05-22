"""Shared signal / cycle state rules for sim and live engines."""
from __future__ import annotations


def events_indicate_bet_opened(events: list[dict]) -> bool:
    """True only when a bet row was created (not dry-run or order-error only)."""
    return any(e.get("type") in ("live_bet_open", "simulation_bet_open") for e in events)


def events_are_order_errors_only(events: list[dict]) -> bool:
    if not events:
        return False
    return all(e.get("type") == "live_order_error" for e in events)
