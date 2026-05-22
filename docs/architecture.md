# Terminal Sirius — Architecture

## Layers

| Layer | Role | Technology |
|-------|------|------------|
| Exchange | Raw market data | Binance WS, Polymarket CLOB |
| Engine | Process, time, aggregate | Nautilus `TradingNode` + Actors + `LiqPolyStrategy` |
| Live execution | Polymarket CLOB orders | Nautilus `PolymarketExecutionClient` (+ credential/retry stack) |
| API (BFF) | Stable contract for UI | FastAPI HTTP + `/ws` |
| UI | Display, light indicators | React |

```
Exchange → Nautilus Actors → Queue → FastAPI → Browser
                ↓
           PostgreSQL (bars, events, sim/live)
```

## Single writer (liquidations)

- **Live path:** `LiquidationActor` (in node) or fallback `liquidation_stream` when Nautilus is unavailable.
- **Parse/aggregate:** [`backend/liquidations.py`](../backend/liquidations.py) only.
- **Persist:** `liquidation_bars` always; `liquidation_events` + watchlist when `PERSIST_LIQUIDATION_EVENTS_TO_DB=1` (default).
## API contract (UI)

UI talks only to FastAPI, not Nautilus or Postgres directly.

| Endpoint / WS | Purpose |
|---------------|---------|
| `GET /klines` | OHLCV |
| `GET /liquidations` | 15m bar totals (chart, sim) |
| `GET /liquidation-events` | Major-coin event list (Liq Signals) |
| `WS /ws` | Live trade, bar, liquidation, polymarket, sim/live events |

## Signal truth (sim / live)

- One cycle per `(binance_symbol, liq_bar_open, side)` — DB unique index + `_signaled` restored from DB on startup.
- `_signaled` is set only after `live_bet_open` / `simulation_bet_open` (not on order errors).
- Stuck open cycles (no unsettled bets) are closed on engine `load_state` via `repair_stuck_*_cycles`.
- Reconcile uses `signal_ts = liq_bar_open + 900`, not wall clock.

## Nautilus alignment

- `nautilus_env.sync_polymarket_env()` maps `POLYMARKET_PRIVATE_KEY` → `POLYMARKET_PK`, etc.
- `node.py` registers `PolymarketLiveExecClientFactory` when `LIVE_ENABLED` + creds.
- Live CLOB buys use `nautilus_bridge.polymarket_exec` (Nautilus credential helpers + retry).
- Signal/cycle rules remain in FastAPI engines until fully ported to `LiqPolyStrategy`.

## Development rule

Before adding a feature: **Does it bypass Nautilus / single liquidation writer?** If yes, redesign or scope as an explicit side tool.
