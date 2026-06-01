# Roadmap — Nautilus-native next steps

Legacy liq→Poly trade (paper sim, live BFF, `LiqPolyStrategy`) was removed in favor of a clean **data-only** `TradingNode`. See [architecture.md](architecture.md).

## Current (shipped)

- Binance + Polymarket **DataClient** on `TradingNode`
- **Actors:** bridge, liquidation, realtime buckets, quote bridge, optional market recorder
- **Idle** `PolymarketExecutionClient` when `POLYMARKET_EXEC_ENABLED` + wallet creds
- UI: charts, liquidation widgets, Polymarket seconds chart — no trade panels

## Next: new strategy (Nautilus-native)

| Step | Nautilus surface | Notes |
|------|------------------|--------|
| 1 | `Strategy` + `StrategyConfig` | New module under `backend/strategies/` |
| 2 | `on_start` / `on_bar` / `on_data` | Subscribe via `subscribe_bars` / `subscribe_data` — no FastAPI bridge |
| 3 | `submit_order` | `PolymarketExecutionClient` on existing node |
| 4 | Fills / positions | `on_order_filled`, `Portfolio`, `Cache` — not custom DB event queues |
| 5 | UI (optional) | Read model from BFF if needed; keep [ws-api-contract.md](ws-api-contract.md) stable |

Official references: Nautilus docs — *Strategies*, *Execution*, *Polymarket adapter*.

## Not planned (avoid)

- `strategy_runtime` + `handle_strategy_events` BFF pattern
- Duplicate sim vs live motors outside Nautilus
- `SandboxExecutionClient` paper path unless you explicitly want simulated exchange again
