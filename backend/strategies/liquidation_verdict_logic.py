"""Pure post-liquidation verdict math (testable, causal)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LiqSide = Literal["LONG", "SHORT"]
Winner = Literal["liquidation", "recovery", "neutral"]
VerdictStatus = Literal["completed", "expired"]
CompletionReason = Literal["liq_threshold", "recovery_threshold", ""]


class VerdictEventIdFactory:
    """Stable unique ids per liquidation print (Binance order id or same-ms sequence)."""

    def __init__(self) -> None:
        self._seq: dict[tuple[str, str, int], int] = {}

    def make(
        self,
        symbol: str,
        liq_side: str,
        ts_ns: int,
        *,
        order_id: int = 0,
    ) -> str:
        coin = symbol.split("USDT")[0]
        if order_id > 0:
            return f"verdict-{ts_ns}-{coin}-{liq_side}-{order_id}"
        key = (symbol, liq_side, ts_ns)
        seq = self._seq.get(key, 0)
        self._seq[key] = seq + 1
        if seq == 0:
            return f"verdict-{ts_ns}-{coin}-{liq_side}"
        return f"verdict-{ts_ns}-{coin}-{liq_side}-s{seq}"


def liquidation_direction(liq_side: LiqSide) -> Literal["up", "down"]:
    """LONG liquidation = sell pressure → down; SHORT = buy pressure → up."""
    return "down" if liq_side == "LONG" else "up"


def recovery_direction(liq_side: LiqSide) -> Literal["up", "down"]:
    return "up" if liq_side == "LONG" else "down"


def directional_move_pct(anchor: float, price: float, direction: Literal["up", "down"]) -> float:
    if anchor <= 0:
        return 0.0
    pct = (price - anchor) / anchor * 100.0
    if direction == "down":
        return max(0.0, -pct)
    return max(0.0, pct)


def dominance_ratio(liq_move_pct: float, recovery_move_pct: float, *, eps: float = 1e-9) -> float:
    winner = max(liq_move_pct, recovery_move_pct)
    loser = min(liq_move_pct, recovery_move_pct)
    if winner <= 0 or loser <= 0:
        return 0.0
    return winner / max(loser, eps)


def area_bias(liq_area: float, recovery_area: float, *, eps: float = 1e-9) -> float:
    total = liq_area + recovery_area
    if total <= eps:
        return 0.0
    return (recovery_area - liq_area) / total


@dataclass
class OpenVerdictEvent:
    event_id: str
    symbol: str
    liq_side: LiqSide
    notional: float
    event_price: float
    event_ts_ns: int
    liq_move_pct: float = 0.0
    recovery_move_pct: float = 0.0
    liq_area: float = 0.0
    recovery_area: float = 0.0
    last_price: float = 0.0
    last_ts_ns: int = 0
    liq_cross_ts_ns: int = 0
    rec_cross_ts_ns: int = 0
    completion_reached: bool = False
    completion_ts_ns: int = 0
    time_to_dominance_sec: float = 0.0
    winner: Winner = "neutral"
    completion_reason: CompletionReason = ""
    dominance_ratio_value: float = 0.0
    area_bias_value: float = 0.0

    def __post_init__(self) -> None:
        self.last_price = self.event_price
        self.last_ts_ns = self.event_ts_ns


@dataclass(frozen=True)
class CompletedVerdict:
    event_id: str
    symbol: str
    liq_side: LiqSide
    notional: float
    event_price: float
    event_ts_ns: int
    winner: Winner
    liq_move_pct: float
    recovery_move_pct: float
    dominance_ratio: float
    time_to_dominance_sec: float
    area_bias: float
    status: VerdictStatus
    completion_reason: CompletionReason = ""


def _integrate_area(
    event: OpenVerdictEvent,
    price: float,
    ts_ns: int,
) -> None:
    if event.last_ts_ns <= 0 or ts_ns <= event.last_ts_ns:
        return
    dt = (ts_ns - event.last_ts_ns) / 1_000_000_000.0
    if dt <= 0:
        return
    anchor = event.event_price
    if anchor <= 0:
        return
    signed_pct = (price - anchor) / anchor * 100.0
    if event.liq_side == "LONG":
        if signed_pct < 0:
            event.liq_area += abs(signed_pct) * dt
        elif signed_pct > 0:
            event.recovery_area += signed_pct * dt
    else:
        if signed_pct > 0:
            event.liq_area += signed_pct * dt
        elif signed_pct < 0:
            event.recovery_area += abs(signed_pct) * dt


def _maybe_complete_event(
    event: OpenVerdictEvent,
    ts_ns: int,
    *,
    liq_move_threshold_pct: float,
    recovery_move_threshold_pct: float,
) -> bool:
    if event.completion_reached:
        return True
    if event.liq_cross_ts_ns == 0 and event.liq_move_pct >= liq_move_threshold_pct:
        event.liq_cross_ts_ns = ts_ns
    if event.rec_cross_ts_ns == 0 and event.recovery_move_pct >= recovery_move_threshold_pct:
        event.rec_cross_ts_ns = ts_ns
    if not event.liq_cross_ts_ns and not event.rec_cross_ts_ns:
        return False
    if event.liq_cross_ts_ns and event.rec_cross_ts_ns:
        if event.liq_cross_ts_ns <= event.rec_cross_ts_ns:
            event.winner = "liquidation"
            event.completion_reason = "liq_threshold"
            event.completion_ts_ns = event.liq_cross_ts_ns
        else:
            event.winner = "recovery"
            event.completion_reason = "recovery_threshold"
            event.completion_ts_ns = event.rec_cross_ts_ns
    elif event.liq_cross_ts_ns:
        event.winner = "liquidation"
        event.completion_reason = "liq_threshold"
        event.completion_ts_ns = event.liq_cross_ts_ns
    else:
        event.winner = "recovery"
        event.completion_reason = "recovery_threshold"
        event.completion_ts_ns = event.rec_cross_ts_ns
    event.completion_reached = True
    event.time_to_dominance_sec = max(
        0.0, (event.completion_ts_ns - event.event_ts_ns) / 1_000_000_000.0
    )
    return True


def update_open_event(
    event: OpenVerdictEvent,
    price: float,
    ts_ns: int,
    *,
    liq_move_threshold_pct: float,
    recovery_move_threshold_pct: float,
) -> CompletedVerdict | None:
    """Advance one open event; return a completed verdict when a move threshold hits."""
    if price <= 0:
        return None

    _integrate_area(event, price, ts_ns)

    liq_dir = liquidation_direction(event.liq_side)
    rec_dir = recovery_direction(event.liq_side)
    event.liq_move_pct = max(
        event.liq_move_pct,
        directional_move_pct(event.event_price, price, liq_dir),
    )
    event.recovery_move_pct = max(
        event.recovery_move_pct,
        directional_move_pct(event.event_price, price, rec_dir),
    )
    event.dominance_ratio_value = dominance_ratio(
        event.liq_move_pct,
        event.recovery_move_pct,
    )
    event.area_bias_value = area_bias(event.liq_area, event.recovery_area)

    _maybe_complete_event(
        event,
        ts_ns,
        liq_move_threshold_pct=liq_move_threshold_pct,
        recovery_move_threshold_pct=recovery_move_threshold_pct,
    )

    event.last_price = price
    event.last_ts_ns = ts_ns

    if event.completion_reached:
        return _completed(event, status="completed")
    return None


def expire_open_event(event: OpenVerdictEvent) -> CompletedVerdict:
    return _completed(event, status="expired")


def _completed(event: OpenVerdictEvent, *, status: VerdictStatus) -> CompletedVerdict:
    return CompletedVerdict(
        event_id=event.event_id,
        symbol=event.symbol,
        liq_side=event.liq_side,
        notional=event.notional,
        event_price=event.event_price,
        event_ts_ns=event.event_ts_ns,
        winner=event.winner,
        liq_move_pct=event.liq_move_pct,
        recovery_move_pct=event.recovery_move_pct,
        dominance_ratio=event.dominance_ratio_value,
        time_to_dominance_sec=event.time_to_dominance_sec,
        area_bias=event.area_bias_value,
        status=status,
        completion_reason=event.completion_reason if status == "completed" else "",
    )


def verdict_passes_gates(
    verdict: CompletedVerdict,
    *,
    min_recovery_move_pct: float,
    max_time_to_completion_sec: float,
    min_area_bias: float,
    required_winner: Winner = "recovery",
) -> bool:
    if verdict.status != "completed":
        return False
    if verdict.winner != required_winner:
        return False
    if verdict.recovery_move_pct < min_recovery_move_pct:
        return False
    if verdict.time_to_dominance_sec > max_time_to_completion_sec:
        return False
    if verdict.area_bias < min_area_bias:
        return False
    return True
