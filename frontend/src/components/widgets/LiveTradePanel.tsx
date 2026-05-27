import { useCallback, useEffect, useMemo, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type {
  FeedMsg,
  LiveBetOpenMsg,
  LiveBetRow,
  LiveBetSettleMsg,
  LiveStatus,
  SimulationSide,
} from "../../types";
import {
  betTimeTooltip,
  format15mBarWindow,
  formatLiqThreshold,
  signalTimestamp,
  formatWallTimeSec,
} from "../../lib/betTiming";
import {
  aggregateAssetStats,
  formatPairs,
  LIVE_COINS,
  MAJOR_COLORS,
  type LiveCoin,
} from "../../lib/liqCoins";
import styles from "./LiveTradePanel.module.css";

const MAX_ROWS = 150;

function sideLabel(side: SimulationSide): string {
  return side === "long" ? "UP" : "DN";
}

function legLabel(side: SimulationSide, leg: number): string {
  return `${side === "long" ? "L" : "S"}${leg}`;
}

function formatThreshold(
  thresholds: Record<string, number> | undefined,
  selected: readonly string[]
): string {
  if (!thresholds) return "";
  return selected
    .filter((a) => thresholds[a] != null)
    .map((a) => `${a} $${(thresholds[a] / 1000).toFixed(0)}k`)
    .join(" · ");
}

type Props = {
  coins: LiveCoin[];
  onConfigChange: (patch: { coins?: LiveCoin[] }) => void;
};

type SideFilter = "long" | "short";
type LegFilter = "l1" | "l2" | "s1" | "s2";

const ALL_SIDES: SideFilter[] = ["long", "short"];
const ALL_LEGS: LegFilter[] = ["l1", "l2", "s1", "s2"];

export function LiveTradePanel({ coins, onConfigChange }: Props) {
  const selected = coins;
  const selectedSet = useMemo(() => new Set<string>(selected), [selected]);
  const [activeSides, setActiveSides] = useState<Set<SideFilter>>(new Set(ALL_SIDES));
  const [activeLegs, setActiveLegs] = useState<Set<LegFilter>>(new Set(ALL_LEGS));

  const toggleSide = (side: SideFilter) => {
    setActiveSides((prev) => {
      const next = new Set(prev);
      if (next.has(side)) {
        if (next.size === 1) return prev;
        next.delete(side);
      } else {
        next.add(side);
      }
      return next;
    });
  };

  const toggleLeg = (leg: LegFilter) => {
    setActiveLegs((prev) => {
      const next = new Set(prev);
      if (next.has(leg)) {
        if (next.size === 1) return prev;
        next.delete(leg);
      } else {
        next.add(leg);
      }
      return next;
    });
  };
  const { subscribe, status } = useFeed();
  const [liveStatus, setLiveStatus] = useState<LiveStatus | null>(null);
  const availableCoins = useMemo(() => {
    const fromApi = liveStatus?.assets;
    if (fromApi?.length) {
      return fromApi.filter((c): c is LiveCoin =>
        (LIVE_COINS as readonly string[]).includes(c)
      );
    }
    return [...LIVE_COINS];
  }, [liveStatus?.assets]);
  const availableSet = useMemo(() => new Set(availableCoins), [availableCoins]);
  const [rows, setRows] = useState<LiveBetRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const assetsParam = selected.join(",");

  const refresh = useCallback(() => {
    const betsUrl = assetsParam
      ? `/live/bets?limit=100&assets=${encodeURIComponent(assetsParam)}`
      : "/live/bets?limit=100";
    Promise.all([
      fetch("/live/status").then((r) => {
        if (!r.ok) throw new Error(`status ${r.status}`);
        return r.json() as Promise<LiveStatus>;
      }),
      fetch(betsUrl).then((r) => {
        if (!r.ok) throw new Error(`bets ${r.status}`);
        return r.json() as Promise<LiveBetRow[]>;
      }),
    ])
      .then(([s, b]) => {
        setLiveStatus(s);
        setRows(
          b
            .map((row) => ({
              ...row,
              side: row.side ?? "long",
              signal_time: row.signal_time ?? row.opened_at,
            }))
            .slice(0, MAX_ROWS)
        );
        setLoadError(null);
      })
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : "load failed");
      });
  }, [assetsParam]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5_000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (!liveStatus?.assets?.length) return;
    const filtered = selected.filter((c) => availableSet.has(c));
    if (filtered.length === 0) {
      onConfigChange({ coins: availableCoins });
    } else if (filtered.length !== selected.length) {
      onConfigChange({ coins: filtered });
    }
  }, [liveStatus?.assets, availableCoins, availableSet, selected, onConfigChange]);

  useEffect(() => {
    return subscribe("*", (msg: FeedMsg) => {
      if (msg.type === "live_bet_open") {
        const m = msg as LiveBetOpenMsg;
        if (!selectedSet.has(m.asset)) return;
        const row: LiveBetRow = {
          id: m.bet_id,
          cycle_id: m.cycle_id,
          side: m.side,
          leg: m.leg,
          candle_open: m.candle_open,
          poly_slug: m.poly_slug,
          poly_series: m.poly_series,
          entry_price: m.entry_price,
          shares: m.shares,
          cost_usd: m.cost_usd,
          outcome: null,
          pnl_usd: null,
          opened_at: m.opened_at,
          signal_time: m.signal_time ?? m.opened_at,
          liq_bar_open: m.liq_bar_open,
          settled_at: null,
          asset: m.asset,
          threshold: m.threshold,
          order_id: m.order_id,
          clob_status: m.clob_status,
        };
        setRows((prev) => [row, ...prev.filter((r) => r.id !== row.id)].slice(0, MAX_ROWS));
        refresh();
      } else if (msg.type === "live_bet_settle") {
        const m = msg as LiveBetSettleMsg;
        setRows((prev) =>
          prev.map((r) =>
            r.id === m.bet_id
              ? {
                  ...r,
                  outcome: m.outcome,
                  pnl_usd: m.pnl_usd,
                  settled_at: m.settled_at,
                }
              : r
          )
        );
        refresh();
      } else if (
        msg.type === "live_signal" ||
        msg.type === "live_cycle_closed" ||
        msg.type === "live_order_error"
      ) {
        refresh();
      }
    });
  }, [subscribe, refresh, selectedSet]);

  const toggleCoin = (coin: LiveCoin) => {
    const next = selectedSet.has(coin)
      ? selected.filter((c) => c !== coin)
      : [...selected, coin];
    if (next.length === 0) return;
    onConfigChange({ coins: next });
  };

  const visibleRows = useMemo(
    () =>
      rows.filter((r) => {
        if (!selectedSet.has(r.asset)) return false;
        const side = r.side ?? "long";
        if (!activeSides.has(side)) return false;
        const legKey = `${side === "long" ? "l" : "s"}${r.leg}` as LegFilter;
        if (!activeLegs.has(legKey)) return false;
        return true;
      }),
    [rows, selectedSet, activeSides, activeLegs]
  );

  const agg = useMemo(
    () => aggregateAssetStats(selected, activeSides, activeLegs, liveStatus?.by_asset_side_leg),
    [selected, activeSides, activeLegs, liveStatus?.by_asset_side_leg]
  );

  const pnl = agg.total_pnl_usd;
  const pnlClass = pnl >= 0 ? styles.pnlPos : styles.pnlNeg;
  const ordersOff = liveStatus && !liveStatus.orders_enabled;
  const execOff =
    liveStatus &&
    liveStatus.orders_enabled &&
    liveStatus.exec_client_ready === false;
  const thresholdLabel = formatThreshold(liveStatus?.thresholds, selected);

  return (
    <div className={styles.root}>
      <div className={`${styles.toolbar} liveTradeToolbar`}>
        <div className={styles.coins}>
          {availableCoins.map((coin) => {
            const on = selectedSet.has(coin);
            const accent = MAJOR_COLORS[coin];
            return (
              <button
                key={coin}
                type="button"
                className={`${styles.coinBtn} ${on ? styles.coinBtnOn : ""}`}
                style={
                  on
                    ? { borderColor: accent, color: accent, background: `${accent}18` }
                    : undefined
                }
                onClick={() => toggleCoin(coin)}
                aria-pressed={on}
              >
                {coin}
              </button>
            );
          })}
        </div>
        <div className={styles.filterGroup}>
          {(["long", "short"] as SideFilter[]).map((side) => {
            const on = activeSides.has(side);
            return (
              <button
                key={side}
                type="button"
                className={`${styles.filterBtn} ${on ? (side === "long" ? styles.filterBtnUp : styles.filterBtnDn) : ""}`}
                onClick={() => toggleSide(side)}
                aria-pressed={on}
              >
                {side === "long" ? "UP" : "DN"}
              </button>
            );
          })}
        </div>
        <div className={styles.filterGroup}>
          {(["l1", "l2", "s1", "s2"] as LegFilter[]).map((leg) => {
            const on = activeLegs.has(leg);
            const isLong = leg.startsWith("l");
            return (
              <button
                key={leg}
                type="button"
                className={`${styles.filterBtn} ${on ? (isLong ? styles.filterBtnUp : styles.filterBtnDn) : ""}`}
                onClick={() => toggleLeg(leg)}
                aria-pressed={on}
              >
                {leg.toUpperCase()}
              </button>
            );
          })}
        </div>
        <span className={styles.liveBadge}>LIVE</span>
        {thresholdLabel && (
          <span className={styles.threshold}>{thresholdLabel}</span>
        )}
        {liveStatus && (
          <>
            <span className={styles.stat}>
              Trades <strong>{agg.total_bets + agg.open_bets}</strong>
            </span>
            <span className={styles.stat}>
              PnL <strong className={pnlClass}>${pnl.toFixed(2)}</strong>
            </span>
            <span className={styles.stat}>
              WR <strong>{agg.win_rate.toFixed(0)}%</strong>
            </span>
            <span className={styles.stat}>
              Open <strong>{agg.open_bets}</strong>
            </span>
            <span className={styles.stat}>
              UP <strong className={styles.sideUp}>{agg.long_open}</strong>
            </span>
            <span className={styles.stat}>
              DN <strong className={styles.sideDn}>{agg.short_open}</strong>
            </span>
          </>
        )}
        {ordersOff && (
          <span className={styles.ordersOff} title="LIVE_ENABLED=false or missing API creds">
            orders off
          </span>
        )}
        {execOff && (
          <span
            className={styles.ordersOff}
            title="Polymarket Nautilus exec client not connected — signals queue until ready"
          >
            exec waiting
          </span>
        )}
        <span className={`${styles.status} ${styles[status]}`}>{status}</span>
        {loadError && (
          <span className={styles.loadError} title={loadError}>
            api err
          </span>
        )}
      </div>

      <div className={styles.list}>
        {visibleRows.length === 0 ? (
          <p className={styles.empty}>
            {loadError
              ? `Cannot load live (${loadError}). Run frontend :3000 + backend :8000.`
              : `${formatPairs(selected)} 15m liq → real Polymarket UP/DN.`}
          </p>
        ) : (
          visibleRows.map((r) => {
            const side = r.side ?? "long";
            return (
              <div key={r.id} className={styles.row}>
                <span className={styles.asset}>{r.asset}</span>
                <span className={side === "long" ? styles.sideUp : styles.sideDn}>
                  {sideLabel(side)}
                </span>
                <span className={styles.leg}>{legLabel(side, r.leg)}</span>
                <span className={styles.open}>
                  {(r.entry_price * 100).toFixed(0)}¢
                </span>
                <span className={styles.meta}>
                  {r.shares.toFixed(0)} sh · ${r.cost_usd.toFixed(2)}
                  {r.threshold != null && Number.isFinite(r.threshold) && (
                    <span className={styles.rowThreshold}>
                      {" "}
                      · {formatLiqThreshold(r.threshold)}
                    </span>
                  )}
                </span>
                <span
                  className={`${styles.outcome} ${
                    r.outcome === "win"
                      ? styles.outcomeWin
                      : r.outcome === "loss"
                        ? styles.outcomeLoss
                        : styles.pending
                  }`}
                >
                  {r.outcome ?? "open"}
                  {r.pnl_usd != null && (
                    <span
                      className={
                        r.pnl_usd >= 0 ? styles.pnlInlinePos : styles.pnlInlineNeg
                      }
                    >
                      {" "}
                      {r.pnl_usd >= 0 ? "+" : ""}${r.pnl_usd.toFixed(2)}
                    </span>
                  )}
                </span>
                <span className={styles.orderId} title={r.order_id ?? ""}>
                  {r.order_id ? r.order_id.slice(0, 8) : "—"}
                </span>
                <span
                  className={styles.timeBlock}
                  title={
                    r.leg > 1
                      ? `${betTimeTooltip(r)} · leg ${r.leg} · order ${r.order_id ?? "—"}`
                      : betTimeTooltip(r)
                  }
                >
                  <span className={styles.timeEvent}>{formatWallTimeSec(signalTimestamp(r))}</span>
                  <span className={styles.timeSep}>·</span>
                  <span className={styles.timeBar15}>
                    {format15mBarWindow(signalTimestamp(r))}
                  </span>
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
