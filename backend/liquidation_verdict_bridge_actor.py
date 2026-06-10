"""Forward completed ``LiquidationVerdict`` events to the FastAPI WS queue."""

from __future__ import annotations

import json
import math
import queue
from typing import TYPE_CHECKING, Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import DataType

from strategies.messages import LiquidationVerdict
from strategies.messages import LiquidationVerdictStatus

if TYPE_CHECKING:
    import multiprocessing


class LiquidationVerdictBridgeActorConfig(ActorConfig, frozen=True):
    max_tape_rows: int = 100


def _json_safe(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return str(obj)


class LiquidationVerdictBridgeActor(Actor):
    def __init__(
        self,
        config: LiquidationVerdictBridgeActorConfig,
        data_queue: queue.Queue | multiprocessing.queues.Queue,
    ) -> None:
        super().__init__(config)
        self._queue = data_queue
        self._tape: list[dict] = []
        self._max_rows = int(config.max_tape_rows)
        self._pending_total = 0
        self._pending_by_symbol: dict[str, int] = {}

    def _enqueue(self, msg: dict) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            pass

    def on_start(self) -> None:
        self.msgbus.subscribe(
            topic=f"data.{DataType(LiquidationVerdict).topic}",
            handler=self.handle_data,
        )
        self.msgbus.subscribe(
            topic=f"data.{DataType(LiquidationVerdictStatus).topic}",
            handler=self.handle_data,
        )
        print("[liquidation-verdict] LiquidationVerdictBridgeActor → liquidation_verdict WS")

    def _enqueue_feed(self, *, verdict: dict | None = None) -> None:
        payload: dict[str, Any] = {
            "type": "liquidation_verdict",
            "tape": self._tape,
            "pending": self._pending_total,
            "pending_by_symbol": self._pending_by_symbol,
        }
        if verdict is not None:
            payload["verdict"] = verdict
        elif self._tape:
            payload["verdict"] = self._tape[0]
        self._enqueue(_json_safe(payload))

    def handle_data(self, data: Data) -> None:
        if isinstance(data, LiquidationVerdictStatus):
            self._pending_total = int(data.pending_total)
            try:
                raw = json.loads(str(data.pending_by_coin_json or "{}"))
                self._pending_by_symbol = {
                    str(k): int(v) for k, v in raw.items() if int(v) > 0
                }
            except (json.JSONDecodeError, TypeError, ValueError):
                self._pending_by_symbol = {}
            self._enqueue_feed()
            return
        if not isinstance(data, LiquidationVerdict):
            return
        symbol = str(data.instrument_id)
        coin = symbol.split("USDT")[0]
        row = {
            "event_id": data.event_id,
            "symbol": coin,
            "liq_side": data.liq_side,
            "notional": round(float(data.notional), 2),
            "event_price": float(data.event_price),
            "winner": data.winner,
            "liq_move_pct": round(float(data.liq_move_pct), 4),
            "recovery_move_pct": round(float(data.recovery_move_pct), 4),
            "dominance_ratio": round(float(data.dominance_ratio), 2),
            "time_to_dominance_sec": round(float(data.time_to_dominance_sec), 2),
            "area_bias": round(float(data.area_bias), 4),
            "status": data.status,
            "completion_reason": str(data.completion_reason or ""),
            "event_time": int(data.ts_event) // 1_000_000_000,
            "ts": int(self.clock.timestamp_ns()),
        }
        self._tape.insert(0, row)
        self._tape = self._tape[: self._max_rows]
        self._enqueue_feed(verdict=row)
