"""
PostgreSQL access layer (asyncpg).

Schema:
  - `klines` — OHLCV bars (symbol, interval, time)
  - `liquidation_bars` — long/short liquidation notional per bar (symbol, interval, time)
  - `liquidation_events` — raw Binance forceOrder JSON per event
  - `liquidation_watchlist_events` — denormalized major-coin liqs (trigger from liquidation_events)
  - `liquidation_verdict_events` — completed live verdict rows (LiquidationVerdictBridge → BFF)
  - `paper_equity_snapshots` — paper-trade equity/PnL curve points (PaperTradeMonitorActor)
  - `paper_events` — paper-trade order/position lifecycle events (activity feed)
"""
import json
import os
from typing import Optional

import asyncpg

LIQUIDATION_EVENTS_MAX_ROWS = 50_000
LIQUIDATION_EVENTS_RETENTION_HOURS = 48
LIQUIDATION_VERDICT_MAX_ROWS = 50_000
LIQUIDATION_VERDICT_RETENTION_HOURS = 168


def _prune_liquidation_verdicts_enabled() -> bool:
    v = os.environ.get("PRUNE_LIQUIDATION_VERDICTS", "0").lower()
    return v in ("1", "true", "yes", "on")
PAPER_EQUITY_MAX_ROWS = 100_000
PAPER_EVENTS_MAX_ROWS = 20_000
_pool: Optional[asyncpg.Pool] = None


async def init() -> None:
    global _pool
    dsn = os.environ.get("DATABASE_URL", "postgresql://sirius:sirius@localhost:5432/sirius")
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    await _migrate(_pool)


async def close() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call db.init() first")
    return _pool


# ── Schema ──────────────────────────────────────────────────────────────────

async def _migrate(p: asyncpg.Pool) -> None:
    async with p.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS klines (
                symbol   TEXT             NOT NULL,
                interval TEXT             NOT NULL,
                time     BIGINT           NOT NULL,  -- unix seconds
                open     DOUBLE PRECISION NOT NULL,
                high     DOUBLE PRECISION NOT NULL,
                low      DOUBLE PRECISION NOT NULL,
                close    DOUBLE PRECISION NOT NULL,
                volume   DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (symbol, interval, time)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS klines_symbol_interval_time
            ON klines (symbol, interval, time DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_bars (
                symbol   TEXT             NOT NULL,
                interval TEXT             NOT NULL,
                time     BIGINT           NOT NULL,
                long     DOUBLE PRECISION NOT NULL DEFAULT 0,
                short    DOUBLE PRECISION NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, interval, time)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS liquidation_bars_symbol_interval_time
            ON liquidation_bars (symbol, interval, time DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_events (
                id          BIGSERIAL PRIMARY KEY,
                trade_id    BIGINT NOT NULL UNIQUE,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload     JSONB NOT NULL
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS liquidation_events_received_at
            ON liquidation_events (received_at DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_watchlist_events (
                trade_id    BIGINT NOT NULL PRIMARY KEY,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                notional    DOUBLE PRECISION NOT NULL,
                time        BIGINT NOT NULL,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS liquidation_watchlist_events_symbol_time
            ON liquidation_watchlist_events (symbol, time DESC)
        """)
        await conn.execute("""
            CREATE OR REPLACE FUNCTION trg_liquidation_events_to_watchlist()
            RETURNS TRIGGER AS $$
            DECLARE
                bin_sym text;
                apv text;
                zv text;
                tv text;
                notional double precision;
                tsec bigint;
            BEGIN
                bin_sym := NEW.payload->'o'->>'s';
                IF bin_sym IS NULL THEN
                    RETURN NEW;
                END IF;
                IF bin_sym NOT IN (
                    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'XRPUSDT',
                    'HYPEUSDT', 'BNBUSDT'
                ) THEN
                    RETURN NEW;
                END IF;
                apv := NEW.payload->'o'->>'ap';
                zv := NEW.payload->'o'->>'z';
                tv := NEW.payload->'o'->>'T';
                IF apv IS NULL OR zv IS NULL OR tv IS NULL THEN
                    RETURN NEW;
                END IF;
                BEGIN
                    notional := apv::double precision * zv::double precision;
                    tsec := (tv::bigint / 1000);
                EXCEPTION WHEN OTHERS THEN
                    RETURN NEW;
                END;
                INSERT INTO liquidation_watchlist_events (
                    trade_id, symbol, side, notional, time, received_at
                )
                VALUES (
                    NEW.trade_id,
                    bin_sym || '-PERP.BINANCE',
                    COALESCE(NEW.payload->'o'->>'S', ''),
                    notional,
                    tsec,
                    NEW.received_at
                )
                ON CONFLICT (trade_id) DO NOTHING;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        await conn.execute("""
            DROP TRIGGER IF EXISTS liquidation_events_watchlist ON liquidation_events
        """)
        await conn.execute("""
            CREATE TRIGGER liquidation_events_watchlist
            AFTER INSERT ON liquidation_events
            FOR EACH ROW EXECUTE FUNCTION trg_liquidation_events_to_watchlist()
        """)
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'liquidation_watchlist_events_trade_id_fkey'
                ) THEN
                    ALTER TABLE liquidation_watchlist_events
                        ADD CONSTRAINT liquidation_watchlist_events_trade_id_fkey
                        FOREIGN KEY (trade_id)
                        REFERENCES liquidation_events (trade_id)
                        ON DELETE CASCADE;
                END IF;
            END $$;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS liquidation_verdict_events (
                event_id               TEXT PRIMARY KEY,
                symbol                 TEXT NOT NULL,
                liq_side               TEXT NOT NULL,
                notional               DOUBLE PRECISION NOT NULL,
                event_price            DOUBLE PRECISION NOT NULL,
                winner                 TEXT NOT NULL,
                liq_move_pct           DOUBLE PRECISION NOT NULL,
                recovery_move_pct      DOUBLE PRECISION NOT NULL,
                dominance_ratio        DOUBLE PRECISION NOT NULL,
                time_to_dominance_sec  DOUBLE PRECISION NOT NULL,
                area_bias              DOUBLE PRECISION NOT NULL,
                status                 TEXT NOT NULL,
                event_time             BIGINT NOT NULL,
                received_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS liquidation_verdict_events_symbol_time
            ON liquidation_verdict_events (symbol, event_time DESC)
        """)
        await conn.execute("""
            ALTER TABLE liquidation_verdict_events
            ADD COLUMN IF NOT EXISTS completion_reason TEXT NOT NULL DEFAULT ''
        """)
        await conn.execute("DROP TABLE IF EXISTS simulation_bets CASCADE")
        await conn.execute("DROP TABLE IF EXISTS simulation_cycles CASCADE")
        await conn.execute("DROP TABLE IF EXISTS live_bets CASCADE")
        await conn.execute("DROP TABLE IF EXISTS live_cycles CASCADE")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
                id             BIGSERIAL PRIMARY KEY,
                ts             BIGINT NOT NULL,            -- nanoseconds (ts_event)
                currency       TEXT,
                equity         DOUBLE PRECISION,
                balance        DOUBLE PRECISION,
                realized_pnl   DOUBLE PRECISION,
                unrealized_pnl DOUBLE PRECISION,
                total_pnl      DOUBLE PRECISION,
                net_exposure   DOUBLE PRECISION,
                open_positions INTEGER,
                open_orders    INTEGER
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS paper_equity_snapshots_ts
            ON paper_equity_snapshots (ts)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_events (
                id            BIGSERIAL PRIMARY KEY,
                ts            BIGINT NOT NULL,
                kind          TEXT NOT NULL,
                instrument_id TEXT,
                side          TEXT,
                quantity      DOUBLE PRECISION,
                price         DOUBLE PRECISION,
                commission    DOUBLE PRECISION,
                realized_pnl  DOUBLE PRECISION,
                payload       JSONB NOT NULL
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS paper_events_ts
            ON paper_events (ts DESC)
        """)


# ── Queries ──────────────────────────────────────────────────────────────────

async def get_klines(
    symbol: str, interval: str, limit: int, before: int | None = None
) -> list[dict]:
    if before is not None:
        rows = await pool().fetch(
            """
            SELECT time, open, high, low, close, volume
            FROM klines
            WHERE symbol = $1 AND interval = $2 AND time < $3
            ORDER BY time DESC
            LIMIT $4
            """,
            symbol, interval, before, limit,
        )
    else:
        rows = await pool().fetch(
            """
            SELECT time, open, high, low, close, volume
            FROM klines
            WHERE symbol = $1 AND interval = $2
            ORDER BY time DESC
            LIMIT $3
            """,
            symbol, interval, limit,
        )
    # Return ascending order (oldest first) — what lightweight-charts expects
    return [dict(r) for r in reversed(rows)]


async def upsert_klines(symbol: str, interval: str, rows: list[dict]) -> None:
    if not rows:
        return
    records = [
        (symbol, interval, r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"])
        for r in rows
    ]
    await pool().executemany(
        """
        INSERT INTO klines (symbol, interval, time, open, high, low, close, volume)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (symbol, interval, time) DO UPDATE
            SET open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume
        """,
        records,
    )


async def upsert_bar(symbol: str, interval: str, bar: dict) -> None:
    await upsert_klines(symbol, interval, [bar])


# ── Liquidations ─────────────────────────────────────────────────────────────

async def get_liquidation_bars(
    symbol: str, interval: str, limit: int, before: int | None = None
) -> list[dict]:
    if before is not None:
        rows = await pool().fetch(
            """
            SELECT time, long, short
            FROM liquidation_bars
            WHERE symbol = $1 AND interval = $2 AND time < $3
            ORDER BY time DESC
            LIMIT $4
            """,
            symbol, interval, before, limit,
        )
    else:
        rows = await pool().fetch(
            """
            SELECT time, long, short
            FROM liquidation_bars
            WHERE symbol = $1 AND interval = $2
            ORDER BY time DESC
            LIMIT $3
            """,
            symbol, interval, limit,
        )
    return [
        {"time": r["time"], "long": float(r["long"]), "short": float(r["short"])}
        for r in reversed(rows)
    ]


async def get_liquidation_bars_range(
    symbol: str,
    interval: str,
    from_time: int,
    to_time: int | None,
    limit: int,
) -> list[dict]:
    if to_time is not None:
        rows = await pool().fetch(
            """
            SELECT time, long, short
            FROM liquidation_bars
            WHERE symbol = $1 AND interval = $2 AND time >= $3 AND time <= $4
            ORDER BY time DESC
            LIMIT $5
            """,
            symbol,
            interval,
            from_time,
            to_time,
            limit,
        )
    else:
        rows = await pool().fetch(
            """
            SELECT time, long, short
            FROM liquidation_bars
            WHERE symbol = $1 AND interval = $2 AND time >= $3
            ORDER BY time DESC
            LIMIT $4
            """,
            symbol,
            interval,
            from_time,
            limit,
        )
    return [
        {"time": r["time"], "long": float(r["long"]), "short": float(r["short"])}
        for r in reversed(rows)
    ]


async def add_liquidation_delta(
    symbol: str,
    interval: str,
    time: int,
    long_delta: float,
    short_delta: float,
) -> None:
    await pool().execute(
        """
        INSERT INTO liquidation_bars (symbol, interval, time, long, short)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (symbol, interval, time) DO UPDATE
            SET long  = liquidation_bars.long + EXCLUDED.long,
                short = liquidation_bars.short + EXCLUDED.short
        """,
        symbol, interval, time, long_delta, short_delta,
    )


async def insert_liquidation_event(trade_id: int, payload: dict) -> bool:
    """Insert raw forceOrder item; return True if new row."""
    row = await pool().fetchrow(
        """
        INSERT INTO liquidation_events (trade_id, payload)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (trade_id) DO NOTHING
        RETURNING id
        """,
        trade_id,
        json.dumps(payload),
    )
    return row is not None


async def get_liquidation_event_payloads(limit: int) -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT payload
        FROM liquidation_events
        ORDER BY id DESC
        LIMIT $1
        """,
        limit,
    )
    out: list[dict] = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            out.append(json.loads(p))
        else:
            out.append(dict(p))
    return out


async def get_liquidation_watchlist_events(
    symbols: list[str], limit: int
) -> list[dict]:
    """Recent denormalized liqs for watchlist symbols (time desc)."""
    rows = await pool().fetch(
        """
        SELECT trade_id, symbol, side, notional, time
        FROM liquidation_watchlist_events
        WHERE symbol = ANY($1::text[])
        ORDER BY time DESC
        LIMIT $2
        """,
        symbols,
        limit,
    )
    return [
        {
            "type": "liquidation",
            "trade_id": int(r["trade_id"]),
            "symbol": r["symbol"],
            "side": r["side"],
            "notional": round(float(r["notional"]), 2),
            "time": int(r["time"]),
        }
        for r in rows
    ]


_prune_event_counter = 0


async def maybe_prune_liquidation_events() -> None:
    global _prune_event_counter
    _prune_event_counter += 1
    if _prune_event_counter % 100 != 0:
        return
    await prune_liquidation_events()


async def prune_liquidation_events() -> None:
    await pool().execute(
        """
        DELETE FROM liquidation_events
        WHERE received_at < NOW() - make_interval(hours => $1::int)
        """,
        LIQUIDATION_EVENTS_RETENTION_HOURS,
    )
    await pool().execute(
        """
        DELETE FROM liquidation_events
        WHERE id NOT IN (
            SELECT id FROM liquidation_events
            ORDER BY id DESC
            LIMIT $1
        )
        """,
        LIQUIDATION_EVENTS_MAX_ROWS,
    )


_verdict_prune_counter = 0


async def insert_liquidation_verdict(row: dict) -> bool:
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        return False
    try:
        await pool().execute(
            """
            INSERT INTO liquidation_verdict_events (
                event_id, symbol, liq_side, notional, event_price,
                winner, liq_move_pct, recovery_move_pct, dominance_ratio,
                time_to_dominance_sec, area_bias, status, completion_reason,
                event_time
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9,
                $10, $11, $12, $13,
                $14
            )
            ON CONFLICT (event_id) DO UPDATE SET
                winner = EXCLUDED.winner,
                liq_move_pct = EXCLUDED.liq_move_pct,
                recovery_move_pct = EXCLUDED.recovery_move_pct,
                dominance_ratio = EXCLUDED.dominance_ratio,
                time_to_dominance_sec = EXCLUDED.time_to_dominance_sec,
                area_bias = EXCLUDED.area_bias,
                status = EXCLUDED.status,
                completion_reason = EXCLUDED.completion_reason,
                received_at = NOW()
            """,
            event_id,
            str(row.get("symbol") or ""),
            str(row.get("liq_side") or ""),
            float(row.get("notional") or 0.0),
            float(row.get("event_price") or 0.0),
            str(row.get("winner") or "neutral"),
            float(row.get("liq_move_pct") or 0.0),
            float(row.get("recovery_move_pct") or 0.0),
            float(row.get("dominance_ratio") or 0.0),
            float(row.get("time_to_dominance_sec") or 0.0),
            float(row.get("area_bias") or 0.0),
            str(row.get("status") or "expired"),
            str(row.get("completion_reason") or ""),
            int(row.get("event_time") or 0),
        )
    except (TypeError, ValueError):
        return False
    if _prune_liquidation_verdicts_enabled():
        await maybe_prune_liquidation_verdicts()
    return True


async def get_liquidation_verdict_events(
    *,
    coins: list[str],
    min_notional: float = 0.0,
    sides: frozenset[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    sides = sides or frozenset({"LONG", "SHORT"})
    sql = """
        SELECT
            event_id, symbol, liq_side, notional, event_price,
            winner, liq_move_pct, recovery_move_pct, dominance_ratio,
            time_to_dominance_sec, area_bias, status, completion_reason,
            event_time
        FROM liquidation_verdict_events
        WHERE symbol = ANY($1::text[])
          AND liq_side = ANY($2::text[])
          AND notional >= $3
        ORDER BY event_time DESC
    """
    args: list = [coins, list(sides), max(0.0, min_notional)]
    if limit is not None and limit > 0:
        sql += " LIMIT $4"
        args.append(max(1, int(limit)))
    rows = await pool().fetch(sql, *args)
    return [
        {
            "event_id": r["event_id"],
            "symbol": r["symbol"],
            "liq_side": r["liq_side"],
            "notional": round(float(r["notional"]), 2),
            "event_price": float(r["event_price"]),
            "winner": r["winner"],
            "liq_move_pct": float(r["liq_move_pct"]),
            "recovery_move_pct": float(r["recovery_move_pct"]),
            "dominance_ratio": float(r["dominance_ratio"]),
            "time_to_dominance_sec": float(r["time_to_dominance_sec"]),
            "area_bias": float(r["area_bias"]),
            "status": r["status"],
            "completion_reason": str(r["completion_reason"] or ""),
            "event_time": int(r["event_time"]),
        }
        for r in rows
    ]


async def get_liquidation_verdict_stats(
    *,
    coins: list[str],
    min_notional: float = 0.0,
    sides: frozenset[str] | None = None,
) -> dict:
    sides = sides or frozenset({"LONG", "SHORT"})
    row = await pool().fetchrow(
        """
        SELECT
            COUNT(*)::int AS count,
            COUNT(*) FILTER (WHERE status = 'completed')::int AS completed,
            COUNT(*) FILTER (WHERE status = 'expired')::int AS expired,
            COUNT(*) FILTER (
                WHERE status = 'completed' AND winner = 'recovery'
            )::int AS recovery_wins,
            AVG(dominance_ratio) FILTER (WHERE status = 'completed') AS avg_dominance,
            AVG(time_to_dominance_sec) FILTER (WHERE status = 'completed') AS avg_time,
            AVG(area_bias) FILTER (WHERE status = 'completed') AS avg_area
        FROM liquidation_verdict_events
        WHERE symbol = ANY($1::text[])
          AND liq_side = ANY($2::text[])
          AND notional >= $3
        """,
        coins,
        list(sides),
        max(0.0, min_notional),
    )
    completed = int(row["completed"] or 0)
    recovery_wins = int(row["recovery_wins"] or 0)
    return {
        "count": int(row["count"] or 0),
        "completed": completed,
        "expired": int(row["expired"] or 0),
        "recovery_rate": (recovery_wins / completed) if completed > 0 else 0.0,
        "avg_dominance": float(row["avg_dominance"] or 0.0),
        "avg_time": float(row["avg_time"] or 0.0),
        "avg_area": float(row["avg_area"] or 0.0),
    }


async def maybe_prune_liquidation_verdicts() -> None:
    if not _prune_liquidation_verdicts_enabled():
        return
    global _verdict_prune_counter
    _verdict_prune_counter += 1
    if _verdict_prune_counter % 100 != 0:
        return
    await prune_liquidation_verdicts()


async def clear_liquidation_verdict_events() -> int:
    """Delete all verdict rows (e.g. after raising min-notional thresholds)."""
    result = await pool().execute("DELETE FROM liquidation_verdict_events")
    # asyncpg returns "DELETE N"
    try:
        return int(result.split()[-1])
    except (AttributeError, IndexError, ValueError):
        return 0


async def prune_liquidation_verdicts() -> None:
    if not _prune_liquidation_verdicts_enabled():
        return
    await pool().execute(
        """
        DELETE FROM liquidation_verdict_events
        WHERE received_at < NOW() - make_interval(hours => $1::int)
        """,
        LIQUIDATION_VERDICT_RETENTION_HOURS,
    )
    await pool().execute(
        """
        DELETE FROM liquidation_verdict_events
        WHERE event_id NOT IN (
            SELECT event_id FROM liquidation_verdict_events
            ORDER BY event_time DESC
            LIMIT $1
        )
        """,
        LIQUIDATION_VERDICT_MAX_ROWS,
    )


# ── Paper-trade monitoring ────────────────────────────────────────────────────


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_paper_equity_counter = 0
_paper_events_counter = 0


async def insert_paper_snapshot(msg: dict) -> None:
    """Persist one equity-curve point from a ``paper_snapshot`` WS message."""
    global _paper_equity_counter
    account = msg.get("account") or {}
    pnl = msg.get("pnl") or {}
    exposure = msg.get("exposure") or {}
    counts = msg.get("counts") or {}
    await pool().execute(
        """
        INSERT INTO paper_equity_snapshots (
            ts, currency, equity, balance, realized_pnl, unrealized_pnl,
            total_pnl, net_exposure, open_positions, open_orders
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        int(msg.get("ts") or 0),
        account.get("currency"),
        _opt_float(account.get("equity")),
        _opt_float(account.get("balance")),
        _opt_float(pnl.get("realized")),
        _opt_float(pnl.get("unrealized")),
        _opt_float(pnl.get("total")),
        _opt_float(exposure.get("net")),
        int(counts.get("open_positions") or 0),
        int(counts.get("open_orders") or 0),
    )
    _paper_equity_counter += 1
    if _paper_equity_counter % 500 == 0:
        await _prune_paper_equity()


async def _prune_paper_equity() -> None:
    await pool().execute(
        """
        DELETE FROM paper_equity_snapshots
        WHERE id NOT IN (
            SELECT id FROM paper_equity_snapshots
            ORDER BY id DESC
            LIMIT $1
        )
        """,
        PAPER_EQUITY_MAX_ROWS,
    )


async def get_paper_equity(since: int | None, limit: int) -> list[dict]:
    """Equity-curve points ascending (oldest first)."""
    if since is not None:
        rows = await pool().fetch(
            """
            SELECT ts, currency, equity, balance, realized_pnl, unrealized_pnl,
                   total_pnl, net_exposure, open_positions, open_orders
            FROM paper_equity_snapshots
            WHERE ts >= $1
            ORDER BY ts DESC
            LIMIT $2
            """,
            since,
            limit,
        )
    else:
        rows = await pool().fetch(
            """
            SELECT ts, currency, equity, balance, realized_pnl, unrealized_pnl,
                   total_pnl, net_exposure, open_positions, open_orders
            FROM paper_equity_snapshots
            ORDER BY ts DESC
            LIMIT $1
            """,
            limit,
        )
    return [
        {
            "ts": int(r["ts"]),
            "currency": r["currency"],
            "equity": _opt_float(r["equity"]),
            "balance": _opt_float(r["balance"]),
            "realized_pnl": _opt_float(r["realized_pnl"]),
            "unrealized_pnl": _opt_float(r["unrealized_pnl"]),
            "total_pnl": _opt_float(r["total_pnl"]),
            "net_exposure": _opt_float(r["net_exposure"]),
            "open_positions": int(r["open_positions"] or 0),
            "open_orders": int(r["open_orders"] or 0),
        }
        for r in reversed(rows)
    ]


async def insert_paper_event(msg: dict) -> None:
    """Persist a ``paper_event`` WS message for the activity feed/history."""
    global _paper_events_counter
    await pool().execute(
        """
        INSERT INTO paper_events (
            ts, kind, instrument_id, side, quantity, price,
            commission, realized_pnl, payload
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        """,
        int(msg.get("ts") or 0),
        str(msg.get("kind") or "unknown"),
        msg.get("instrument_id"),
        msg.get("side"),
        _opt_float(msg.get("quantity")),
        _opt_float(msg.get("price")),
        _opt_float(msg.get("commission")),
        _opt_float(msg.get("realized_pnl")),
        json.dumps(msg),
    )
    _paper_events_counter += 1
    if _paper_events_counter % 200 == 0:
        await _prune_paper_events()


async def _prune_paper_events() -> None:
    await pool().execute(
        """
        DELETE FROM paper_events
        WHERE id NOT IN (
            SELECT id FROM paper_events
            ORDER BY id DESC
            LIMIT $1
        )
        """,
        PAPER_EVENTS_MAX_ROWS,
    )


async def get_paper_events(limit: int) -> list[dict]:
    """Recent paper-trade events, newest first."""
    rows = await pool().fetch(
        """
        SELECT payload
        FROM paper_events
        ORDER BY id DESC
        LIMIT $1
        """,
        limit,
    )
    out: list[dict] = []
    for r in rows:
        p = r["payload"]
        out.append(json.loads(p) if isinstance(p, str) else dict(p))
    return out
