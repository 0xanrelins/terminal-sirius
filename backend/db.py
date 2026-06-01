"""
PostgreSQL access layer (asyncpg).

Schema:
  - `klines` — OHLCV bars (symbol, interval, time)
  - `liquidation_bars` — long/short liquidation notional per bar (symbol, interval, time)
  - `liquidation_events` — raw Binance forceOrder JSON per event
  - `liquidation_watchlist_events` — denormalized major-coin liqs (trigger from liquidation_events)
"""
import json
import os
from typing import Optional

import asyncpg

LIQUIDATION_EVENTS_MAX_ROWS = 50_000
LIQUIDATION_EVENTS_RETENTION_HOURS = 48

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
        await conn.execute("DROP TABLE IF EXISTS simulation_bets CASCADE")
        await conn.execute("DROP TABLE IF EXISTS simulation_cycles CASCADE")
        await conn.execute("DROP TABLE IF EXISTS live_bets CASCADE")
        await conn.execute("DROP TABLE IF EXISTS live_cycles CASCADE")


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
