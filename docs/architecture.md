# Terminal Sirius — Architecture

## Layers

| Layer | Role | Technology |
|-------|------|------------|
| Exchange | Raw market data | Binance WS, Polymarket CLOB |
| Engine | Process, time, aggregate | Nautilus `TradingNode` + Actors |
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
- Reconcile uses `signal_ts = liq_bar_open + 900`, not wall clock.

## Development rule

Before adding a feature: **Does it bypass Nautilus / single liquidation writer?** If yes, redesign or scope as an explicit side tool.
