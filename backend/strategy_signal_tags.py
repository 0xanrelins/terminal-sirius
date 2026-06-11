"""Encode/decode paper strategy context on Nautilus order ``tags``."""

from __future__ import annotations

import re
from typing import Any

_RECOVERY_EXIT_REASON = re.compile(r"^recovery_exit_(?P<pct>\d+(?:p\d+)?)$")
_LIQUIDATION_EXIT_REASON = re.compile(r"^liquidation_exit_(?P<pct>\d+(?:p\d+)?)$")
_TIME_STOP_REASON = re.compile(r"^time_stop_(?P<sec>\d+)s$")

ENTRY_SIGNAL_TAG_PREFIX = "ts-sig:"
EXIT_REASON_TAG_PREFIX = "ts-exit:"
PAPER_ENTRY_TAG_PREFIX = "paper-sig:"
PAPER_EXIT_TAG_PREFIX = "paper-exit:"


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


def build_paper_entry_signal_tags(
    *,
    strategy_id: str,
    symbol: str,
    direction: str,
    reason: str | None = None,
    context: dict[str, object] | None = None,
) -> list[str]:
    """Strategy-agnostic entry tag for new paper strategies."""
    fields: dict[str, object] = {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "direction": direction,
    }
    if reason:
        fields["reason"] = reason
    if context:
        fields.update({k: v for k, v in context.items() if v is not None})
    return [PAPER_ENTRY_TAG_PREFIX + _encode_fields(fields)]


def recovery_exit_reason(recovery_exit_pct: float) -> str:
    """Canonical close reason for recovery exits, e.g. 0.2 -> ``recovery_exit_0p2``."""
    pct_s = f"{recovery_exit_pct:.6f}".rstrip("0").rstrip(".")
    return f"recovery_exit_{pct_s.replace('.', 'p')}"


def liquidation_exit_reason(liquidation_exit_pct: float) -> str:
    """Canonical close reason for adverse liquidation moves, e.g. 0.2 -> ``liquidation_exit_0p2``."""
    pct_s = f"{liquidation_exit_pct:.6f}".rstrip("0").rstrip(".")
    return f"liquidation_exit_{pct_s.replace('.', 'p')}"


def time_stop_reason(seconds: int) -> str:
    """Canonical close reason for hold timeouts, e.g. 200 -> ``time_stop_200s``."""
    return f"time_stop_{int(seconds)}s"


def recovery_exit_pct_from_reason(reason: str | None) -> float | None:
    """Parse recovery threshold percent from a close reason string."""
    if not reason:
        return None
    match = _RECOVERY_EXIT_REASON.match(reason.strip())
    if match is None:
        return None
    try:
        return float(match.group("pct").replace("p", "."))
    except ValueError:
        return None


def format_recovery_exit_label(recovery_exit_pct: float) -> str:
    """Compact UI label, e.g. ``REC 0.2%``."""
    pct_s = f"{recovery_exit_pct:.6f}".rstrip("0").rstrip(".")
    return f"REC {pct_s}%"


def build_exit_reason_tags(*, reason: str) -> list[str]:
    """Single tag string for strategy-driven position close orders."""
    safe_reason = (reason or "").strip()
    if not safe_reason:
        return []
    return [f"{EXIT_REASON_TAG_PREFIX}reason={safe_reason}"]


def build_paper_exit_reason_tags(
    *,
    strategy_id: str,
    reason: str,
    symbol: str | None = None,
    direction: str | None = None,
) -> list[str]:
    """Strategy-agnostic exit tag for new paper strategies."""
    safe_reason = (reason or "").strip()
    if not safe_reason:
        return []
    fields: dict[str, object] = {
        "strategy_id": strategy_id,
        "reason": safe_reason,
    }
    if symbol:
        fields["symbol"] = symbol
    if direction:
        fields["direction"] = direction
    return [PAPER_EXIT_TAG_PREFIX + _encode_fields(fields)]


def _encode_fields(fields: dict[str, object]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        safe_key = str(key).strip()
        if not safe_key:
            continue
        safe_value = str(value).replace(";", ",").strip()
        parts.append(f"{safe_key}={safe_value}")
    return ";".join(parts)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def parse_entry_signal_tag(tags: Any) -> dict[str, str] | None:
    """Parse the first paper entry tag from Nautilus order tags.

    Returns normalized aliases so legacy callers can keep reading ``sym``/``dir``
    while new strategies can use ``symbol``/``direction``.
    """
    if tags is None:
        return None
    if isinstance(tags, str):
        candidates = [tags]
    elif isinstance(tags, (list, tuple)):
        candidates = [str(t) for t in tags]
    else:
        return None

    raw: str | None = None
    prefix: str | None = None
    for tag in candidates:
        if tag.startswith(PAPER_ENTRY_TAG_PREFIX):
            raw = tag
            prefix = PAPER_ENTRY_TAG_PREFIX
            break
        if tag.startswith(ENTRY_SIGNAL_TAG_PREFIX):
            raw = tag
            prefix = ENTRY_SIGNAL_TAG_PREFIX
            break
    if raw is None:
        return None

    body = raw[len(prefix or "") :]
    out = _parse_fields(body)
    if "symbol" in out and "sym" not in out:
        out["sym"] = out["symbol"]
    if "sym" in out and "symbol" not in out:
        out["symbol"] = out["sym"]
    if "direction" in out and "dir" not in out:
        out["dir"] = out["direction"]
    if "dir" in out and "direction" not in out:
        out["direction"] = out["dir"]
    return out or None


def parse_exit_reason_tag(tags: Any) -> str | None:
    """Parse the first paper exit reason from Nautilus order tags."""
    if tags is None:
        return None
    if isinstance(tags, str):
        candidates = [tags]
    elif isinstance(tags, (list, tuple)):
        candidates = [str(t) for t in tags]
    else:
        return None

    raw: str | None = None
    prefix: str | None = None
    for tag in candidates:
        if tag.startswith(PAPER_EXIT_TAG_PREFIX):
            raw = tag
            prefix = PAPER_EXIT_TAG_PREFIX
            break
        if tag.startswith(EXIT_REASON_TAG_PREFIX):
            raw = tag
            prefix = EXIT_REASON_TAG_PREFIX
            break
    if raw is None:
        return None

    body = raw[len(prefix or "") :]
    reason = _parse_fields(body).get("reason")
    if reason:
        return reason
    return None


def _parse_fields(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in body.split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def format_entry_signal_display(parsed: dict[str, str] | None) -> str:
    """Compact Closed-tab label."""
    if not parsed:
        return "—"

    if parsed.get("strategy_id"):
        strategy = parsed.get("strategy_id") or "paper"
        direction = parsed.get("direction") or parsed.get("dir") or "?"
        reason = parsed.get("reason")
        return " · ".join(p for p in (strategy, direction, reason) if p)

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
