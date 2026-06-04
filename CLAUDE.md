# CLAUDE.md — Terminal Sirius

Personal Bloomberg-style trading terminal. **NautilusTrader is the non-negotiable core.**
Read this first so you don't need to scan the whole tree.

## Golden rule

Before adding/changing anything in the trading path, name the native NautilusTrader
component that supports it (docs: https://github.com/nautechsystems/nautilus_trader;
local source: `~/Documents/nautilus_trader`). No custom wrappers, monkey-patches, or
workarounds — if Nautilus lacks it, say so and surface the official path. See
[docs/architecture.md](docs/architecture.md) §"Development rule".

## Two halves (keep them separate)

1. **Trading core — 100% native Nautilus**, runs in a child process
   (`backend/node.py` → `run_node_in_process`, multiprocessing spawn). `TradingNode`
   + native factories, `Strategy`/`Actor`, native indicators, `BinanceFuturesLiquidation`,
   `StreamingConfig` → `ParquetDataCatalog`, `BacktestRunConfig`. Orders go **only**
   through a Nautilus ExecClient/Sandbox.
2. **UI / BFF data plane — outside Nautilus by design**, runs in the FastAPI parent
   (`backend/main.py`). Drains a `multiprocessing.Queue` from the actors, broadcasts WS,
   serves charts. Deliberately bypasses Nautilus because Nautilus doesn't offer these:
   `klines.py` (Binance REST history), `liquidations.py` (time-bucket aggregation),
   `chart_indicators.py` (mirrors the frontend), `adapters/polymarket/gamma.py` (market
   search). Persists to its **own** Postgres schema (`db.py`), separate from Nautilus cache.
   **Do not "fix" these to be native** — they are accepted side-tools.

## Map

| Path | Role |
|------|------|
| `backend/node.py` | TradingNode factory + actor/strategy wiring (native) |
| `backend/main.py` | FastAPI BFF: REST + `/ws`, queue→WS, Postgres persist |
| `backend/strategies/` | `TerminalSiriusStrategy`, signal actors, custom `Indicator`s, `messages.py` (custom `Data` for Actor→Strategy) |
| `backend/adapters/polymarket/` | Polymarket glue: `gamma` (REST), `rolling` (15m slug math), `quote_bridge_actor` (quotes + `ActivePolymarketMarket`), `messages` |
| `backend/recorders/` | catalog `StreamingConfig`, liquidation persistence |
| `backend/catalog/` | `ParquetDataCatalog` factory (live capture under `backend/catalog/`, gitignored) |
| `backend/backtest/` | native `BacktestRunConfig` (catalog replay) |
| `frontend/src/` | React UI (`FeedContext` = single WS; widgets under `components/widgets/`) |

## Commands

```bash
docker compose up -d                      # Postgres
cd backend && ./scripts/run_backend.sh    # backend → logs/uvicorn.log
cd backend && .venv/bin/python -m pytest tests/ -q          # backend tests
cd backend && .venv/bin/python scripts/run_terminal_sirius_backtest.py   # backtest
cd frontend && npm run dev | npm run build | npm run test  # UI (vite/vitest)
```

## Notes

- Heavy dirs are gitignored (`.venv`, `node_modules`, `backend/catalog/data`+`live`,
  `backend/logs`); ripgrep skips them. The legacy parquet snapshot lives **outside** the
  repo at `~/Documents/sirius-archive/` (`CATALOG_USE_ARCHIVE=1` to read it).
- Known gap: backtest can't execute Polymarket trades yet — nothing publishes
  `ActivePolymarketMarket` during catalog replay (the quote bridge is live/UI-only).
