import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import {
  POST_EVENT_COINS,
  type PostEventCoin,
} from "../../lib/liqPostEventChart";
import {
  emptyVerdictStats,
  formatVerdictNotional,
  normalizeVerdictCoins,
  normalizeVerdictSides,
  completionReasonLabel,
  verdictFetchUrl,
  verdictStatsUrl,
  winnerLabel,
  type LiquidationVerdictMsg,
  type LiquidationVerdictRow,
  type VerdictSide,
  type LiquidationVerdictStats,
} from "../../lib/liquidationVerdict";
import styles from "./LiquidationVerdictDashboard.module.css";

type Props = {
  coins?: PostEventCoin[];
  sides?: VerdictSide[];
  onConfigChange: (patch: { coins?: PostEventCoin[]; sides?: VerdictSide[] }) => void;
};

function formatPct(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function mergeVerdictRows(
  ...groups: LiquidationVerdictRow[][]
): LiquidationVerdictRow[] {
  const byId = new Map<string, LiquidationVerdictRow>();
  for (const group of groups) {
    for (const row of group) {
      byId.set(row.event_id, row);
    }
  }
  return Array.from(byId.values()).sort((a, b) => b.event_time - a.event_time);
}

function VerdictRow({ row }: { row: LiquidationVerdictRow }) {
  const winnerClass =
    row.winner === "recovery"
      ? styles.winnerRecovery
      : row.winner === "liquidation"
        ? styles.winnerLiq
        : styles.winnerNeutral;
  const statusClass =
    row.status === "completed" ? styles.statusCompleted : styles.statusExpired;
  return (
    <div className={styles.row}>
      <span>{row.symbol}</span>
      <span>{row.liq_side}</span>
      <span>{formatVerdictNotional(row.notional)}</span>
      <span className={winnerClass}>{winnerLabel(row.winner)}</span>
      <span>{row.dominance_ratio.toFixed(2)}x</span>
      <span>{row.time_to_dominance_sec.toFixed(0)}s</span>
      <span className={styles.details}>
        L {formatPct(row.liq_move_pct)} / R {formatPct(row.recovery_move_pct)} ·{" "}
        {completionReasonLabel(row.completion_reason)} · A {row.area_bias.toFixed(2)} · Px{" "}
        {row.event_price.toFixed(1)} ·{" "}
        <span className={statusClass}>{row.status}</span>
      </span>
    </div>
  );
}

export function LiquidationVerdictDashboard({
  coins: coinsProp,
  sides: sidesProp,
  onConfigChange,
}: Props) {
  const coins = normalizeVerdictCoins(coinsProp);
  const sides = normalizeVerdictSides(sidesProp);
  const coinSet = new Set(coins);
  const sideSet = new Set(sides);
  const { subscribe, status } = useFeed();

  const [liveRows, setLiveRows] = useState<LiquidationVerdictRow[]>([]);
  const [persistedRows, setPersistedRows] = useState<LiquidationVerdictRow[]>([]);
  const [cumulativeStats, setCumulativeStats] =
    useState<LiquidationVerdictStats>(emptyVerdictStats);
  const [pending, setPending] = useState(0);
  const [pendingBySymbol, setPendingBySymbol] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refreshStatsRef = useRef<(() => Promise<void>) | undefined>(undefined);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(verdictStatsUrl({ coins, sides }));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as LiquidationVerdictStats;
      setCumulativeStats(data);
    } catch {
      /* keep last cumulative stats */
    }
  }, [coins, sides]);

  refreshStatsRef.current = fetchStats;

  const fetchPersisted = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(verdictFetchUrl({ coins, sides, limit: 0 }));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { verdicts: LiquidationVerdictRow[] };
      setPersistedRows(data.verdicts ?? []);
      setLiveRows([]);
      await fetchStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Fetch failed");
    } finally {
      setLoading(false);
    }
  }, [coins, sides, fetchStats]);

  useEffect(() => {
    return subscribe("*", (msg) => {
      if (msg.type !== "liquidation_verdict") return;
      const payload = msg as LiquidationVerdictMsg;
      if (payload.pending !== undefined) setPending(payload.pending);
      if (payload.pending_by_symbol) setPendingBySymbol(payload.pending_by_symbol);

      const verdict = payload.verdict;
      if (!verdict?.event_id || !coinSet.has(verdict.symbol as PostEventCoin)) return;
      if (!sideSet.has(verdict.liq_side)) return;

      setLiveRows((prev) => mergeVerdictRows(prev, [verdict]));
      void refreshStatsRef.current?.();
    });
  }, [subscribe, coins.join(","), sides.join(",")]);

  useEffect(() => {
    void fetchPersisted();
    const id = window.setInterval(() => void fetchPersisted(), 60_000);
    return () => window.clearInterval(id);
  }, [fetchPersisted]);

  const displayRows = useMemo(() => {
    const filteredPersisted = persistedRows.filter((r) =>
      coinSet.has(r.symbol as PostEventCoin) && sideSet.has(r.liq_side)
    );
    const filteredLive = liveRows.filter((r) =>
      coinSet.has(r.symbol as PostEventCoin) && sideSet.has(r.liq_side)
    );
    return mergeVerdictRows(filteredPersisted, filteredLive);
  }, [persistedRows, liveRows, coins.join(","), sides.join(",")]);

  const pendingSelected = useMemo(() => {
    return coins.reduce((sum, coin) => sum + (pendingBySymbol[coin] ?? 0), 0);
  }, [coins, pendingBySymbol]);

  const pendingBreakdown = useMemo(() => {
    return coins
      .filter((coin) => (pendingBySymbol[coin] ?? 0) > 0)
      .map((coin) => `${coin}:${pendingBySymbol[coin]}`)
      .join(" ");
  }, [coins, pendingBySymbol]);

  function toggleCoin(coin: PostEventCoin) {
    const next = coins.includes(coin)
      ? coins.filter((c) => c !== coin)
      : [...coins, coin];
    onConfigChange({ coins: next.length ? next : ["BTC"] });
  }

  function toggleSide(side: VerdictSide) {
    const next = sides.includes(side)
      ? sides.filter((s) => s !== side)
      : [...sides, side];
    onConfigChange({ sides: next.length ? next : ["LONG", "SHORT"] });
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        {POST_EVENT_COINS.map((coin) => (
          <button
            key={coin}
            type="button"
            className={`${styles.coinBtn} ${coins.includes(coin) ? styles.coinBtnOn : ""}`}
            onClick={() => toggleCoin(coin)}
          >
            {coin}
          </button>
        ))}
        <div className={styles.sideDivider} />
        {(["LONG", "SHORT"] as const).map((side) => (
          <button
            key={side}
            type="button"
            className={`${styles.coinBtn} ${sides.includes(side) ? styles.coinBtnOn : ""}`}
            onClick={() => toggleSide(side)}
          >
            {side}
          </button>
        ))}
      </div>

      <div className={styles.body}>
        <div className={styles.stats}>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>Observing</div>
            <div className={styles.statValue}>{pendingSelected}</div>
            {pendingBreakdown ? (
              <div className={styles.statHint}>{pendingBreakdown}</div>
            ) : null}
          </div>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>Events</div>
            <div className={styles.statValue}>{cumulativeStats.count}</div>
            <div className={styles.statHint}>
              done {cumulativeStats.completed} · expired {cumulativeStats.expired}
            </div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>Recovery rate</div>
            <div className={styles.statValue}>
              {(cumulativeStats.recovery_rate * 100).toFixed(0)}%
            </div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>Avg L/R ratio</div>
            <div className={styles.statValue}>
              {cumulativeStats.avg_dominance.toFixed(1)}x
            </div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statLabel}>Avg time / area</div>
            <div className={styles.statValue}>
              {cumulativeStats.avg_time.toFixed(0)}s ·{" "}
              {cumulativeStats.avg_area.toFixed(2)}
            </div>
          </div>
        </div>

        <div className={styles.panel}>
          <div className={styles.panelTitle}>
            Verdict tape · L≥0.2% / R≥0.2% · 450s
          </div>
          <div className={`${styles.row} ${styles.rowHeader}`}>
            <span>coin</span>
            <span>side</span>
            <span>$</span>
            <span>winner</span>
            <span>dom</span>
            <span>time</span>
            <span>path values</span>
          </div>
          <div className={styles.list}>
            {loading && displayRows.length === 0 ? (
              <div className={styles.empty}>Loading…</div>
            ) : displayRows.length === 0 ? (
              <div className={styles.empty}>Waiting for liquidation verdicts…</div>
            ) : (
              displayRows.map((row) => (
                <VerdictRow key={row.event_id} row={row} />
              ))
            )}
          </div>
        </div>
      </div>

      <div className={styles.status}>
        WS {status} · observing {pendingSelected}/{pending} · {displayRows.length}{" "}
        listed · {cumulativeStats.count} total
        {error ? ` · ${error}` : ""}
      </div>
    </div>
  );
}
