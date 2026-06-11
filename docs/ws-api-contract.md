# WebSocket & REST contract (frozen)

**Do not break without updating:** `frontend/src/types.ts`, `backend/ws_contract.py`, and this file.

## WebSocket `GET /ws`

JSON messages; one object per frame. Optional `?symbols=` filters by `symbol`.

### Market data

| `type` | Producer | Notes |
|--------|----------|--------|
| `trade` | `BridgeActor` | Binance perp ticks |
| `bar` | `BridgeActor` / `RealtimeBucketActor` | OHLCV; `interval` e.g. `15m` or live `1s`/`5s` |
| `indicator` | `RealtimeBucketActor` | EMA / session VWAP / rolling VWAP point on `1s`/`5s` bucket close |
| `polymarket` | `PolymarketQuoteBridgeActor` | `yes_price` 0–1; optional `bid`/`ask` |
| `quote` | `BridgeActor` | Optional; Binance BBO |
| `liquidation` | `LiquidationUiBridgeActor` | Optional `bars[]` snapshot |

### Paper-trade monitoring

Account-level; **no `symbol`** (broadcast to all clients). Emitted only when
`STRATEGY_ENABLED=true` by `PaperTradeMonitorActor` (native `Portfolio` + `Cache`
+ `PortfolioAnalyzer`).

| `type` | Producer | Notes |
|--------|----------|--------|
| `paper_snapshot` | `PaperTradeMonitorActor` | Periodic (default 2s, `PAPER_SNAPSHOT_INTERVAL_SEC`): `run`, `account`, `pnl`, `exposure`, `positions[]`, `orders[]`, `stats`, `counts` |
| `paper_event` | `PaperTradeMonitorActor` | `kind` ∈ `fill`/`position_open`/`position_close`/`position_change`/`order_rejected`/`order_denied` |

### Strategy signal monitoring

Account-level; **no `symbol`** (broadcast to all clients). Emitted only when
`STRATEGY_ENABLED=true` by `StrategySignalBridgeActor` (msgbus custom data +
`events.order.*`).

| `type` | Producer | Notes |
|--------|----------|--------|
| `strategy_signal_snapshot` | `StrategySignalBridgeActor` | Periodic (default 2s, `STRATEGY_SIGNAL_SNAPSHOT_INTERVAL_SEC`): FreshPaper run meta (`strategy_id`, `trade_enabled`, exit/hold limits) + `symbols` map keyed by Binance perp id (Polymarket window, liq triggers, optional VWAP/verdict) |

TypeScript union: `FeedMsg` in `frontend/src/types.ts`.

## REST (BFF)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/klines` | Historical OHLCV |
| GET | `/liquidations` | 15m liq bar totals |
| GET | `/liquidation-events` | Major-coin liq events |
| GET | `/liq-post-event/sessions` | Post-liq 30m % sessions from ParquetDataCatalog |
| GET | `/polymarket/markets` | Gamma search |
| GET | `/polymarket/presets` | Rolling 15m presets |
| GET | `/paper/equity` | Equity/PnL curve points (`?since=` ns) |
| GET | `/paper/events` | Recent paper-trade events (`?limit=`) |

### `GET /liq-post-event/sessions`

Query params: `symbols` (comma coins), `interval` (`30s` only; legacy `1s`/`5s` accepted), `min_notional`, `sides` (`LONG`,`SHORT`), optional `limit` (omit = all matching events in 7d lookback).

Response: `{ sessions: [{ session_id, symbol, side, notional, anchor_price, event_time, status, points[] }] }`.

## Change process

1. Update `types.ts` + `ws_contract.py` + this doc in the **same PR**.
2. Run `backend/scripts/run_smoke_tests.sh`.
3. Manual smoke: `docs/nautilus-migration-smoke.md`.
