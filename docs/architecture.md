# Terminal Sirius — Architecture

## Layers

| Layer | Role | Technology |
|-------|------|------------|
| Exchange | Raw market data | Binance WS; Polymarket via Nautilus `PolymarketDataClient` (+ quote bridge to UI) |
| Engine | Process, time, aggregate | Nautilus `TradingNode` + Actors (no strategies registered) |
| Execution (idle) | Polymarket CLOB | Nautilus `PolymarketExecutionClient` when `POLYMARKET_EXEC_ENABLED` + creds |
| API (BFF) | Stable contract for UI | FastAPI HTTP + `/ws` |
| UI | Display, charts, liquidation | React |

```
Exchange → Nautilus Actors → Queue → FastAPI → Browser
                ↓
           PostgreSQL (klines, liquidation bars/events)
```

## Single writer (liquidations)

- **Live path:** `LiquidationActor` (in node) or fallback `liquidation_stream` when Nautilus is unavailable.
- **Parse/aggregate:** [`backend/liquidations.py`](../backend/liquidations.py) only.
- **Persist:** `liquidation_bars` always; `liquidation_events` + watchlist when `PERSIST_LIQUIDATION_EVENTS_TO_DB=1` (default).

## API contract (UI)

Frozen WS/REST shapes: [ws-api-contract.md](ws-api-contract.md) (`frontend/src/types.ts`, `backend/ws_contract.py`).

UI talks only to FastAPI, not Nautilus or Postgres directly.

| Endpoint / WS | Purpose |
|---------------|---------|
| `GET /klines` | OHLCV |
| `GET /liquidations` | 15m bar totals (charts) |
| `GET /liquidation-events` | Major-coin event list (Liq Signals) |
| `GET /liq-post-event/sessions` | Post-liq catalog sessions |
| `GET /polymarket/*` | Search, presets, subscribe |
| `WS /ws` | Trade, bar, liquidation, polymarket |

## Liq→Poly trading (removed)

The legacy `LiqPolyStrategy` stack (paper sim, live bets, BFF persist bridge) was removed. Future strategies should register on `TradingNode` and use native `Strategy.submit_order` + `PolymarketExecutionClient` only.

See [Tam Nautilus entegrasyonu — plan](tam-nautilus-entegrasyonu-plan.md) for the target model.

## Nautilus alignment (current)

- `TradingNode`: Binance + Polymarket data clients; optional idle `PolymarketExecutionClient`.
- Actors: `BridgeActor`, `RealtimeBucketActor`, `LiquidationActor`, `PolymarketQuoteBridgeActor`, `PolymarketRealtimeBucketActor`, optional `MarketRecorderActor`.
- Polymarket env/L2: `nautilus_env.prepare_polymarket_env()` at FastAPI startup.
- Polymarket UI quotes: `PolymarketQuoteBridgeActor` (`POLYMARKET_DATA_ENABLED=false` disables).

## Development rule

Before adding a feature: **Does it bypass Nautilus / single liquidation writer?** If yes, redesign or scope as an explicit side tool.
