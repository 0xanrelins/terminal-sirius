"""On-demand Nautilus execution reports (Cache → ReportProvider) for paper/live strategy."""

from __future__ import annotations

import queue
from datetime import timedelta
from typing import Any

import pandas as pd
from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.identifiers import Venue

POLYMARKET_VENUE = Venue("POLYMARKET")


class StrategyReportActorConfig(ActorConfig, frozen=True):
    poll_interval_sec: float = 0.25


def dataframe_to_json_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialize a ReportProvider DataFrame for JSON REST responses."""
    if df is None or df.empty:
        return []
    out = df.copy()
    if out.index.name is not None or not isinstance(out.index, pd.RangeIndex):
        out = out.reset_index()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str)
    return out.where(pd.notnull(out), None).to_dict(orient="records")


class StrategyReportActor(Actor):
    """
    Drains parent command queue and emits native Trader-equivalent reports on the data queue.

    Uses ``ReportProvider`` + ``Cache`` (same data as ``Trader.generate_*_report``).
    """

    def __init__(
        self,
        config: StrategyReportActorConfig,
        command_queue: queue.Queue,
        data_queue: queue.Queue,
        *,
        paper_trade: bool,
    ) -> None:
        super().__init__(config)
        self._command_queue = command_queue
        self._data_queue = data_queue
        self._paper_trade = paper_trade

    def on_start(self) -> None:
        interval = max(0.05, float(self.config.poll_interval_sec))
        self.clock.set_timer(
            "poll_strategy_report_commands",
            timedelta(seconds=interval),
            callback=self._on_poll_commands,
        )

    def on_stop(self) -> None:
        self.clock.cancel_timer("poll_strategy_report_commands")

    def _on_poll_commands(self, _event) -> None:
        while True:
            try:
                cmd = self._command_queue.get_nowait()
            except queue.Empty:
                break
            if cmd.get("cmd") != "strategy_report":
                continue
            request_id = cmd.get("request_id")
            if not request_id:
                continue
            self._emit_report(str(request_id))

    def _emit_report(self, request_id: str) -> None:
        payload = self._build_payload()
        msg = {
            "type": "strategy_report",
            "request_id": request_id,
            "payload": payload,
        }
        try:
            self._data_queue.put_nowait(msg)
        except queue.Full:
            self.log.warning("strategy report dropped: data queue full")

    def _build_payload(self) -> dict[str, Any]:
        orders = self.cache.orders()
        positions = self.cache.positions()
        snapshots = self.cache.position_snapshots()
        account = self.cache.account_for_venue(venue=POLYMARKET_VENUE)

        account_report = (
            ReportProvider.generate_account_report(account)
            if account is not None
            else pd.DataFrame()
        )

        return {
            "trader_id": str(self.trader_id),
            "paper_trade": self._paper_trade,
            "venue": str(POLYMARKET_VENUE),
            "ts_event": self.clock.timestamp_ns(),
            "positions": dataframe_to_json_records(
                ReportProvider.generate_positions_report(positions, snapshots),
            ),
            "orders": dataframe_to_json_records(
                ReportProvider.generate_orders_report(orders),
            ),
            "order_fills": dataframe_to_json_records(
                ReportProvider.generate_order_fills_report(orders),
            ),
            "fills": dataframe_to_json_records(
                ReportProvider.generate_fills_report(orders),
            ),
            "account": dataframe_to_json_records(account_report),
        }
