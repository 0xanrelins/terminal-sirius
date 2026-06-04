"""Signal names for Actor → Strategy messaging via ``publish_signal`` / ``subscribe_signal``."""

from __future__ import annotations

import types

# Per-symbol suffix: f"{LIQ_LONG_TRIGGER}:{symbol}" etc.
LIQ_LONG_TRIGGER = "liq_long_trigger"
LIQ_SHORT_TRIGGER = "liq_short_trigger"
VWAP_SNAPSHOT = "vwap_snapshot"
SLOPE_SNAPSHOT = "slope_snapshot"
ZONE_SNAPSHOT = "zone_snapshot"


signals = types.SimpleNamespace(
    LIQ_LONG_TRIGGER=LIQ_LONG_TRIGGER,
    LIQ_SHORT_TRIGGER=LIQ_SHORT_TRIGGER,
    VWAP_SNAPSHOT=VWAP_SNAPSHOT,
    SLOPE_SNAPSHOT=SLOPE_SNAPSHOT,
    ZONE_SNAPSHOT=ZONE_SNAPSHOT,
)


def signal_name(base: str, symbol: str) -> str:
    return f"{base}:{symbol}"
