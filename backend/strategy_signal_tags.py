"""Encode/decode Terminal Sirius entry context on Nautilus order ``tags``."""

from __future__ import annotations

from typing import Any

ENTRY_SIGNAL_TAG_PREFIX = "ts-sig:"


def build_entry_signal_tags(
    *,
    symbol: str,
    direction: str,
    vwap: float | None,
    slope: float | None,
    low_zone: float | None,
    high_zone: float | None,
    last_price: float | None,
    liq_long: bool,
    liq_short: bool,
) -> list[str]:
    """Single tag string for ``OrderFactory.market(..., tags=...)``."""
    payload = (
        f"{ENTRY_SIGNAL_TAG_PREFIX}"
        f"sym={symbol};dir={direction};"
        f"vwap={_fmt(vwap)};slope={_fmt(slope)};"
        f"lo={_fmt(low_zone)};hi={_fmt(high_zone)};px={_fmt(last_price)};"
        f"ll={1 if liq_long else 0};ls={1 if liq_short else 0}"
    )
    return [payload]


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def parse_entry_signal_tag(tags: Any) -> dict[str, str] | None:
    """Parse the first ``ts-sig:`` tag from Nautilus order tags."""
    if tags is None:
        return None
    if isinstance(tags, str):
        candidates = [tags]
    elif isinstance(tags, (list, tuple)):
        candidates = [str(t) for t in tags]
    else:
        return None

    raw: str | None = None
    for tag in candidates:
        if tag.startswith(ENTRY_SIGNAL_TAG_PREFIX):
            raw = tag
            break
    if raw is None:
        return None

    body = raw[len(ENTRY_SIGNAL_TAG_PREFIX) :]
    out: dict[str, str] = {}
    for part in body.split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        out[key.strip()] = val.strip()
    return out or None


def format_entry_signal_display(parsed: dict[str, str] | None) -> str:
    """Compact Closed-tab label."""
    if not parsed:
        return "—"

    slope_s = parsed.get("slope", "-")
    slope_n = _try_float(slope_s)
    if slope_n is not None:
        if slope_n > 0:
            slope_label = f"s+{slope_n:.3f}"
        elif slope_n < 0:
            slope_label = f"s{slope_n:.3f}"
        else:
            slope_label = "s0"
    else:
        slope_label = "s?"

    liq_parts: list[str] = []
    if parsed.get("ll") == "1":
        liq_parts.append("liqL")
    if parsed.get("ls") == "1":
        liq_parts.append("liqS")
    liq = " ".join(liq_parts) if liq_parts else "liq—"

    vwap = parsed.get("vwap", "-")
    px = parsed.get("px", "-")
    lo = parsed.get("lo", "-")
    hi = parsed.get("hi", "-")

    return f"{slope_label} {liq} · v{vwap} · px{px} [{lo}-{hi}]"


def format_entry_signal_tooltip(parsed: dict[str, str] | None) -> str:
    if not parsed:
        return ""
    parts = [f"{k}={v}" for k, v in sorted(parsed.items())]
    return "Entry signal @ market submit: " + ", ".join(parts)


def entry_signal_from_order_tags(tags: Any) -> tuple[str, str]:
    """Return (display, tooltip) for report/UI rows."""
    parsed = parse_entry_signal_tag(tags)
    return format_entry_signal_display(parsed), format_entry_signal_tooltip(parsed)


def _try_float(raw: str) -> float | None:
    if raw in ("", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None
