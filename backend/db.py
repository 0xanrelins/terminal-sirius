"""
PostgreSQL access layer (asyncpg).

Schema:
  - `klines` — OHLCV bars (symbol, interval, time)
  - `liquidation_bars` — long/short liquidation notional per bar (symbol, interval, time)
"""
import os
from typing import Optional

import asyncpg

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
            CREATE TABLE IF NOT EXISTS simulation_cycles (
                id                   BIGSERIAL PRIMARY KEY,
                asset                TEXT NOT NULL,
                binance_symbol       TEXT NOT NULL,
                poly_series          TEXT NOT NULL,
                signal_time          BIGINT NOT NULL,
                signal_long_notional DOUBLE PRECISION NOT NULL,
                threshold            DOUBLE PRECISION NOT NULL,
                status               TEXT NOT NULL DEFAULT 'open',
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS simulation_cycles_status_asset
            ON simulation_cycles (status, asset)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS simulation_bets (
                id           BIGSERIAL PRIMARY KEY,
                cycle_id     BIGINT NOT NULL REFERENCES simulation_cycles(id),
                leg          SMALLINT NOT NULL,
                candle_open  BIGINT NOT NULL,
                poly_slug    TEXT NOT NULL,
                poly_series  TEXT NOT NULL,
                entry_price  DOUBLE PRECISION NOT NULL,
                shares       DOUBLE PRECISION NOT NULL,
                cost_usd     DOUBLE PRECISION NOT NULL,
                outcome      TEXT,
                pnl_usd      DOUBLE PRECISION,
                opened_at    BIGINT NOT NULL,
                settled_at   BIGINT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS simulation_bets_cycle_id
            ON simulation_bets (cycle_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS simulation_bets_opened_at
            ON simulation_bets (opened_at DESC)
        """)
        await conn.execute("""
            ALTER TABLE simulation_cycles
            ADD COLUMN IF NOT EXISTS side TEXT NOT NULL DEFAULT 'long'
        """)
        await conn.execute("""
            ALTER TABLE simulation_cycles
            ADD COLUMN IF NOT EXISTS signal_short_notional DOUBLE PRECISION
        """)
        await conn.execute("""
            ALTER TABLE simulation_bets
            ADD COLUMN IF NOT EXISTS side TEXT NOT NULL DEFAULT 'long'
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS simulation_cycles_status_asset_side
            ON simulation_cycles (status, asset, side)
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


# ── Simulation ───────────────────────────────────────────────────────────────

async def create_simulation_cycle(
    asset: str,
    binance_symbol: str,
    poly_series: str,
    signal_time: int,
    side: str,
    signal_notional: float,
    threshold: float,
) -> int:
    signal_long = signal_notional if side == "long" else 0.0
    signal_short = signal_notional if side == "short" else None
    row = await pool().fetchrow(
        """
        INSERT INTO simulation_cycles
            (asset, binance_symbol, poly_series, signal_time, side,
             signal_long_notional, signal_short_notional, threshold, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'open')
        RETURNING id
        """,
        asset,
        binance_symbol,
        poly_series,
        signal_time,
        side,
        signal_long,
        signal_short,
        threshold,
    )
    return int(row["id"])


async def close_simulation_cycle(cycle_id: int) -> None:
    await pool().execute(
        "UPDATE simulation_cycles SET status = 'closed' WHERE id = $1",
        cycle_id,
    )


async def insert_simulation_bet(
    cycle_id: int,
    leg: int,
    side: str,
    candle_open: int,
    poly_slug: str,
    poly_series: str,
    entry_price: float,
    shares: float,
    cost_usd: float,
    opened_at: int,
) -> int:
    row = await pool().fetchrow(
        """
        INSERT INTO simulation_bets
            (cycle_id, leg, side, candle_open, poly_slug, poly_series,
             entry_price, shares, cost_usd, opened_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        cycle_id,
        leg,
        side,
        candle_open,
        poly_slug,
        poly_series,
        entry_price,
        shares,
        cost_usd,
        opened_at,
    )
    return int(row["id"])


async def settle_simulation_bet(
    bet_id: int,
    outcome: str,
    pnl_usd: float,
    settled_at: int,
) -> None:
    await pool().execute(
        """
        UPDATE simulation_bets
        SET outcome = $2, pnl_usd = $3, settled_at = $4
        WHERE id = $1
        """,
        bet_id,
        outcome,
        pnl_usd,
        settled_at,
    )


async def get_open_simulation_cycles() -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT c.id, c.asset, c.binance_symbol, c.poly_series, c.signal_time,
               c.side, c.signal_long_notional, c.signal_short_notional,
               c.threshold, c.status, c.created_at
        FROM simulation_cycles c
        WHERE c.status = 'open'
        ORDER BY c.id
        """
    )
    return [dict(r) for r in rows]


async def get_open_bets_for_cycles() -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
               b.entry_price, b.shares, b.cost_usd, b.opened_at,
               c.asset, c.binance_symbol
        FROM simulation_bets b
        JOIN simulation_cycles c ON c.id = b.cycle_id
        WHERE c.status = 'open' AND b.settled_at IS NULL
        ORDER BY b.id
        """
    )
    return [dict(r) for r in rows]


async def get_simulation_bets(limit: int = 100) -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
               b.entry_price, b.shares, b.cost_usd, b.outcome, b.pnl_usd,
               b.opened_at, b.settled_at, c.asset, c.signal_time
        FROM simulation_bets b
        JOIN simulation_cycles c ON c.id = b.cycle_id
        ORDER BY b.opened_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def get_simulation_status() -> dict:
    stats = await pool().fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE settled_at IS NOT NULL) AS total_bets,
            COUNT(*) FILTER (WHERE outcome = 'win') AS wins,
            COALESCE(SUM(pnl_usd) FILTER (WHERE settled_at IS NOT NULL), 0) AS total_pnl,
            COUNT(*) FILTER (WHERE settled_at IS NULL) AS open_bets
        FROM simulation_bets
        """
    )
    active_cycles = await pool().fetchval(
        "SELECT COUNT(*) FROM simulation_cycles WHERE status = 'open'"
    )
    by_side_rows = await pool().fetch(
        """
        SELECT COALESCE(side, 'long') AS side,
               COUNT(*) FILTER (WHERE settled_at IS NOT NULL) AS total_bets,
               COUNT(*) FILTER (WHERE outcome = 'win') AS wins,
               COALESCE(SUM(pnl_usd) FILTER (WHERE settled_at IS NOT NULL), 0) AS pnl,
               COUNT(*) FILTER (WHERE settled_at IS NULL) AS open_bets
        FROM simulation_bets
        GROUP BY side
        """
    )
    by_side: dict[str, dict] = {}
    for r in by_side_rows:
        side = str(r["side"])
        tb = int(r["total_bets"] or 0)
        w = int(r["wins"] or 0)
        by_side[side] = {
            "total_bets": tb,
            "wins": w,
            "losses": tb - w,
            "win_rate": round(w / tb * 100, 1) if tb else 0.0,
            "total_pnl_usd": round(float(r["pnl"] or 0), 4),
            "open_bets": int(r["open_bets"] or 0),
        }
    total = int(stats["total_bets"] or 0)
    wins = int(stats["wins"] or 0)
    return {
        "total_bets": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(wins / total * 100, 1) if total else 0.0,
        "total_pnl_usd": round(float(stats["total_pnl"] or 0), 4),
        "open_bets": int(stats["open_bets"] or 0),
        "active_cycles": int(active_cycles or 0),
        "by_side": by_side,
    }
