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

async def _dedupe_cycles_before_unique_index(conn: asyncpg.Connection, prefix: str) -> None:
    """Drop duplicate (binance_symbol, liq_bar_open, side) cycles; keep lowest id."""
    bets = f"{prefix}_bets"
    cycles = f"{prefix}_cycles"
    await conn.execute(
        f"""
        DELETE FROM {bets} b
        USING {cycles} c
        WHERE b.cycle_id = c.id
          AND c.id IN (
            SELECT id FROM (
              SELECT id,
                ROW_NUMBER() OVER (
                  PARTITION BY binance_symbol, liq_bar_open, side
                  ORDER BY id
                ) AS rn
              FROM {cycles}
              WHERE liq_bar_open IS NOT NULL
            ) t WHERE rn > 1
          )
        """
    )
    await conn.execute(
        f"""
        DELETE FROM {cycles}
        WHERE id IN (
          SELECT id FROM (
            SELECT id,
              ROW_NUMBER() OVER (
                PARTITION BY binance_symbol, liq_bar_open, side
                ORDER BY id
              ) AS rn
            FROM {cycles}
            WHERE liq_bar_open IS NOT NULL
          ) t WHERE rn > 1
        )
        """
    )


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
        await conn.execute("""
            ALTER TABLE simulation_cycles
            ADD COLUMN IF NOT EXISTS liq_bar_open BIGINT
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS live_cycles (
                id                   BIGSERIAL PRIMARY KEY,
                asset                TEXT NOT NULL,
                binance_symbol       TEXT NOT NULL,
                poly_series          TEXT NOT NULL,
                signal_time          BIGINT NOT NULL,
                side                 TEXT NOT NULL DEFAULT 'long',
                signal_long_notional DOUBLE PRECISION NOT NULL,
                signal_short_notional DOUBLE PRECISION,
                threshold            DOUBLE PRECISION NOT NULL,
                status               TEXT NOT NULL DEFAULT 'open',
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS live_cycles_status_asset_side
            ON live_cycles (status, asset, side)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS live_bets (
                id           BIGSERIAL PRIMARY KEY,
                cycle_id     BIGINT NOT NULL REFERENCES live_cycles(id),
                leg          SMALLINT NOT NULL,
                side         TEXT NOT NULL DEFAULT 'long',
                candle_open  BIGINT NOT NULL,
                poly_slug    TEXT NOT NULL,
                poly_series  TEXT NOT NULL,
                entry_price  DOUBLE PRECISION NOT NULL,
                shares       DOUBLE PRECISION NOT NULL,
                cost_usd     DOUBLE PRECISION NOT NULL,
                order_id     TEXT,
                clob_status  TEXT,
                fill_price   DOUBLE PRECISION,
                outcome      TEXT,
                pnl_usd      DOUBLE PRECISION,
                opened_at    BIGINT NOT NULL,
                settled_at   BIGINT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS live_bets_cycle_id ON live_bets (cycle_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS live_bets_opened_at ON live_bets (opened_at DESC)
        """)
        await conn.execute("""
            ALTER TABLE live_cycles
            ADD COLUMN IF NOT EXISTS liq_bar_open BIGINT
        """)
        await _dedupe_cycles_before_unique_index(conn, "simulation")
        await _dedupe_cycles_before_unique_index(conn, "live")
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS simulation_cycles_liq_bar_side_uniq
            ON simulation_cycles (binance_symbol, liq_bar_open, side)
            WHERE liq_bar_open IS NOT NULL
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS live_cycles_liq_bar_side_uniq
            ON live_cycles (binance_symbol, liq_bar_open, side)
            WHERE liq_bar_open IS NOT NULL
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
            ORDER BY time ASC
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
            ORDER BY time ASC
            LIMIT $4
            """,
            symbol,
            interval,
            from_time,
            limit,
        )
    return [
        {"time": r["time"], "long": float(r["long"]), "short": float(r["short"])}
        for r in rows
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


# ── Simulation ───────────────────────────────────────────────────────────────

async def create_simulation_cycle(
    asset: str,
    binance_symbol: str,
    poly_series: str,
    signal_time: int,
    side: str,
    signal_notional: float,
    threshold: float,
    liq_bar_open: int | None = None,
) -> int:
    signal_long = signal_notional if side == "long" else 0.0
    signal_short = signal_notional if side == "short" else None
    row = await pool().fetchrow(
        """
        INSERT INTO simulation_cycles
            (asset, binance_symbol, poly_series, signal_time, side,
             signal_long_notional, signal_short_notional, threshold, liq_bar_open, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open')
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
        liq_bar_open,
    )
    return int(row["id"])


async def close_simulation_cycle(cycle_id: int) -> None:
    await pool().execute(
        "UPDATE simulation_cycles SET status = 'closed' WHERE id = $1",
        cycle_id,
    )


async def repair_stuck_simulation_cycles() -> int:
    """Close open sim cycles with no unsettled bets."""
    rows = await pool().fetch(
        """
        UPDATE simulation_cycles c
        SET status = 'closed'
        WHERE c.status = 'open'
          AND NOT EXISTS (
              SELECT 1 FROM simulation_bets b
              WHERE b.cycle_id = c.id AND b.settled_at IS NULL
          )
        RETURNING c.id
        """
    )
    return len(rows)


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


async def get_simulation_signaled_keys(since_liq_bar_open: int) -> list[tuple[str, int, str]]:
    """(binance_symbol, liq_bar_open, side) for dedupe restore after restart."""
    rows = await pool().fetch(
        """
        SELECT binance_symbol, liq_bar_open, side
        FROM simulation_cycles
        WHERE liq_bar_open IS NOT NULL AND liq_bar_open >= $1
        """,
        since_liq_bar_open,
    )
    return [
        (str(r["binance_symbol"]), int(r["liq_bar_open"]), str(r["side"] or "long"))
        for r in rows
    ]


async def get_live_signaled_keys(since_liq_bar_open: int) -> list[tuple[str, int, str]]:
    rows = await pool().fetch(
        """
        SELECT binance_symbol, liq_bar_open, side
        FROM live_cycles
        WHERE liq_bar_open IS NOT NULL AND liq_bar_open >= $1
        """,
        since_liq_bar_open,
    )
    return [
        (str(r["binance_symbol"]), int(r["liq_bar_open"]), str(r["side"] or "long"))
        for r in rows
    ]


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


def _side_stats_row(r) -> dict:
    tb = int(r["total_bets"] or 0)
    w = int(r["wins"] or 0)
    return {
        "total_bets": tb,
        "wins": w,
        "losses": tb - w,
        "win_rate": round(w / tb * 100, 1) if tb else 0.0,
        "total_pnl_usd": round(float(r["pnl"] or 0), 4),
        "open_bets": int(r["open_bets"] or 0),
    }


def _build_by_asset(by_asset_rows) -> dict[str, dict]:
    return {str(r["asset"]): _side_stats_row(r) for r in by_asset_rows}


def _build_by_asset_side(by_asset_side_rows) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in by_asset_side_rows:
        asset = str(r["asset"])
        side = str(r["side"])
        out.setdefault(asset, {})[side] = _side_stats_row(r)
    return out


async def get_simulation_bets(
    limit: int = 100, assets: list[str] | None = None
) -> list[dict]:
    if assets:
        rows = await pool().fetch(
            """
            SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
                   b.entry_price, b.shares, b.cost_usd, b.outcome, b.pnl_usd,
                   b.opened_at, b.settled_at, c.asset, c.signal_time, c.liq_bar_open, c.threshold
            FROM simulation_bets b
            JOIN simulation_cycles c ON c.id = b.cycle_id
            WHERE c.asset = ANY($1::text[])
            ORDER BY b.opened_at DESC
            LIMIT $2
            """,
            assets,
            limit,
        )
    else:
        rows = await pool().fetch(
            """
            SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
                   b.entry_price, b.shares, b.cost_usd, b.outcome, b.pnl_usd,
                   b.opened_at, b.settled_at, c.asset, c.signal_time, c.liq_bar_open, c.threshold
            FROM simulation_bets b
            JOIN simulation_cycles c ON c.id = b.cycle_id
            ORDER BY b.opened_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def clear_simulation_history() -> dict[str, int]:
    """Remove all paper-sim cycles and bets; reset id sequences."""
    async with pool().acquire() as conn:
        bets = await conn.fetchval("SELECT COUNT(*) FROM simulation_bets")
        cycles = await conn.fetchval("SELECT COUNT(*) FROM simulation_cycles")
        await conn.execute(
            "TRUNCATE simulation_bets, simulation_cycles RESTART IDENTITY"
        )
    return {"bets_deleted": int(bets or 0), "cycles_deleted": int(cycles or 0)}


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
        by_side[side] = _side_stats_row(r)
    by_asset_rows = await pool().fetch(
        """
        SELECT c.asset,
               COUNT(*) FILTER (WHERE b.settled_at IS NOT NULL) AS total_bets,
               COUNT(*) FILTER (WHERE b.outcome = 'win') AS wins,
               COALESCE(SUM(b.pnl_usd) FILTER (WHERE b.settled_at IS NOT NULL), 0) AS pnl,
               COUNT(*) FILTER (WHERE b.settled_at IS NULL) AS open_bets
        FROM simulation_bets b
        JOIN simulation_cycles c ON c.id = b.cycle_id
        GROUP BY c.asset
        """
    )
    by_asset_side_rows = await pool().fetch(
        """
        SELECT c.asset, COALESCE(b.side, 'long') AS side,
               COUNT(*) FILTER (WHERE b.settled_at IS NOT NULL) AS total_bets,
               COUNT(*) FILTER (WHERE b.outcome = 'win') AS wins,
               COALESCE(SUM(b.pnl_usd) FILTER (WHERE b.settled_at IS NOT NULL), 0) AS pnl,
               COUNT(*) FILTER (WHERE b.settled_at IS NULL) AS open_bets
        FROM simulation_bets b
        JOIN simulation_cycles c ON c.id = b.cycle_id
        GROUP BY c.asset, b.side
        """
    )
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
        "by_asset": _build_by_asset(by_asset_rows),
        "by_asset_side": _build_by_asset_side(by_asset_side_rows),
    }


# ── Live trading ─────────────────────────────────────────────────────────────

async def create_live_cycle(
    asset: str,
    binance_symbol: str,
    poly_series: str,
    signal_time: int,
    side: str,
    signal_notional: float,
    threshold: float,
    liq_bar_open: int | None = None,
) -> int:
    signal_long = signal_notional if side == "long" else 0.0
    signal_short = signal_notional if side == "short" else None
    row = await pool().fetchrow(
        """
        INSERT INTO live_cycles
            (asset, binance_symbol, poly_series, signal_time, side,
             signal_long_notional, signal_short_notional, threshold, liq_bar_open, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open')
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
        liq_bar_open,
    )
    return int(row["id"])


async def close_live_cycle(cycle_id: int) -> None:
    await pool().execute(
        "UPDATE live_cycles SET status = 'closed' WHERE id = $1",
        cycle_id,
    )


async def repair_stuck_live_cycles() -> int:
    """
    Close open live cycles that have no unsettled bets (e.g. leg-2 order failed).
    Returns number of cycles closed.
    """
    rows = await pool().fetch(
        """
        UPDATE live_cycles c
        SET status = 'closed'
        WHERE c.status = 'open'
          AND NOT EXISTS (
              SELECT 1 FROM live_bets b
              WHERE b.cycle_id = c.id AND b.settled_at IS NULL
          )
        RETURNING c.id, c.asset, c.side
        """
    )
    return len(rows)


async def insert_live_bet(
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
    order_id: str | None = None,
    clob_status: str | None = None,
    fill_price: float | None = None,
) -> int:
    row = await pool().fetchrow(
        """
        INSERT INTO live_bets
            (cycle_id, leg, side, candle_open, poly_slug, poly_series,
             entry_price, shares, cost_usd, opened_at, order_id, clob_status, fill_price)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
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
        order_id,
        clob_status,
        fill_price,
    )
    return int(row["id"])


async def settle_live_bet(
    bet_id: int,
    outcome: str,
    pnl_usd: float,
    settled_at: int,
) -> None:
    await pool().execute(
        """
        UPDATE live_bets
        SET outcome = $2, pnl_usd = $3, settled_at = $4
        WHERE id = $1
        """,
        bet_id,
        outcome,
        pnl_usd,
        settled_at,
    )


async def get_open_live_cycles() -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT c.id, c.asset, c.binance_symbol, c.poly_series, c.signal_time,
               c.side, c.signal_long_notional, c.signal_short_notional,
               c.threshold, c.status, c.created_at
        FROM live_cycles c
        WHERE c.status = 'open'
        ORDER BY c.id
        """
    )
    return [dict(r) for r in rows]


async def get_open_live_bets_for_cycles() -> list[dict]:
    rows = await pool().fetch(
        """
        SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
               b.entry_price, b.shares, b.cost_usd, b.opened_at, b.order_id, b.clob_status,
               b.fill_price, c.asset, c.binance_symbol
        FROM live_bets b
        JOIN live_cycles c ON c.id = b.cycle_id
        WHERE c.status = 'open' AND b.settled_at IS NULL
        ORDER BY b.id
        """
    )
    return [dict(r) for r in rows]


async def get_live_bets(
    limit: int = 100, assets: list[str] | None = None
) -> list[dict]:
    if assets:
        rows = await pool().fetch(
            """
            SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
                   b.entry_price, b.shares, b.cost_usd, b.outcome, b.pnl_usd,
                   b.opened_at, b.settled_at, b.order_id, b.clob_status, b.fill_price,
                   c.asset, c.signal_time, c.liq_bar_open, c.threshold
            FROM live_bets b
            JOIN live_cycles c ON c.id = b.cycle_id
            WHERE c.asset = ANY($1::text[])
            ORDER BY b.opened_at DESC
            LIMIT $2
            """,
            assets,
            limit,
        )
    else:
        rows = await pool().fetch(
            """
            SELECT b.id, b.cycle_id, b.leg, b.side, b.candle_open, b.poly_slug, b.poly_series,
                   b.entry_price, b.shares, b.cost_usd, b.outcome, b.pnl_usd,
                   b.opened_at, b.settled_at, b.order_id, b.clob_status, b.fill_price,
                   c.asset, c.signal_time, c.liq_bar_open, c.threshold
            FROM live_bets b
            JOIN live_cycles c ON c.id = b.cycle_id
            ORDER BY b.opened_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def get_live_status() -> dict:
    stats = await pool().fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE settled_at IS NOT NULL) AS total_bets,
            COUNT(*) FILTER (WHERE outcome = 'win') AS wins,
            COALESCE(SUM(pnl_usd) FILTER (WHERE settled_at IS NOT NULL), 0) AS total_pnl,
            COUNT(*) FILTER (WHERE settled_at IS NULL) AS open_bets
        FROM live_bets
        """
    )
    active_cycles = await pool().fetchval(
        "SELECT COUNT(*) FROM live_cycles WHERE status = 'open'"
    )
    by_side_rows = await pool().fetch(
        """
        SELECT COALESCE(side, 'long') AS side,
               COUNT(*) FILTER (WHERE settled_at IS NOT NULL) AS total_bets,
               COUNT(*) FILTER (WHERE outcome = 'win') AS wins,
               COALESCE(SUM(pnl_usd) FILTER (WHERE settled_at IS NOT NULL), 0) AS pnl,
               COUNT(*) FILTER (WHERE settled_at IS NULL) AS open_bets
        FROM live_bets
        GROUP BY side
        """
    )
    by_side: dict[str, dict] = {}
    for r in by_side_rows:
        side = str(r["side"])
        by_side[side] = _side_stats_row(r)
    by_asset_rows = await pool().fetch(
        """
        SELECT c.asset,
               COUNT(*) FILTER (WHERE b.settled_at IS NOT NULL) AS total_bets,
               COUNT(*) FILTER (WHERE b.outcome = 'win') AS wins,
               COALESCE(SUM(b.pnl_usd) FILTER (WHERE b.settled_at IS NOT NULL), 0) AS pnl,
               COUNT(*) FILTER (WHERE b.settled_at IS NULL) AS open_bets
        FROM live_bets b
        JOIN live_cycles c ON c.id = b.cycle_id
        GROUP BY c.asset
        """
    )
    by_asset_side_rows = await pool().fetch(
        """
        SELECT c.asset, COALESCE(b.side, 'long') AS side,
               COUNT(*) FILTER (WHERE b.settled_at IS NOT NULL) AS total_bets,
               COUNT(*) FILTER (WHERE b.outcome = 'win') AS wins,
               COALESCE(SUM(b.pnl_usd) FILTER (WHERE b.settled_at IS NOT NULL), 0) AS pnl,
               COUNT(*) FILTER (WHERE b.settled_at IS NULL) AS open_bets
        FROM live_bets b
        JOIN live_cycles c ON c.id = b.cycle_id
        GROUP BY c.asset, b.side
        """
    )
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
        "by_asset": _build_by_asset(by_asset_rows),
        "by_asset_side": _build_by_asset_side(by_asset_side_rows),
    }
