import { useCallback, useEffect, useMemo, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type {
  FeedMsg,
  SimulationBetOpenMsg,
  SimulationBetRow,
  SimulationBetSettleMsg,
  SimulationSide,
  SimulationStatus,
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
  MAJOR_COLORS,
  SIM_COINS,
  type SimCoin,
} from "../../lib/liqCoins";
import styles from "./SimulationPanel.module.css";

const MAX_ROWS = 150;

function sideLabel(side: SimulationSide): string {
  return side === "long" ? "UP" : "DN";
}

function legLabel(side: SimulationSide, leg: number): string {
  return `${side === "long" ? "L" : "S"}${leg}`;
}

type SideFilter = "long" | "short";
type LegFilter = "l1" | "l2" | "s1" | "s2";

const ALL_SIDES: SideFilter[] = ["long", "short"];
const ALL_LEGS: LegFilter[] = ["l1", "l2", "s1", "s2"];

type Props = {
  coins: SimCoin[];
  onConfigChange: (patch: { coins?: SimCoin[] }) => void;
};

export function SimulationPanel({ coins, onConfigChange }: Props) {
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
  const [simStatus, setSimStatus] = useState<SimulationStatus | null>(null);
  const availableCoins = useMemo(() => {
    const fromApi = simStatus?.assets;
    if (fromApi?.length) {
      return fromApi.filter((c): c is SimCoin =>
        (SIM_COINS as readonly string[]).includes(c)
      );
    }
    return [...SIM_COINS];
  }, [simStatus?.assets]);
  const availableSet = useMemo(() => new Set(availableCoins), [availableCoins]);
  const [rows, setRows] = useState<SimulationBetRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const assetsParam = selected.join(",");

  const refresh = useCallback(() => {
    const betsUrl = assetsParam
      ? `/simulation/bets?limit=100&assets=${encodeURIComponent(assetsParam)}`
      : "/simulation/bets?limit=100";
    Promise.all([
      fetch("/simulation/status").then((r) => {
        if (!r.ok) throw new Error(`status ${r.status}`);
        return r.json() as Promise<SimulationStatus>;
      }),
      fetch(betsUrl).then((r) => {
        if (!r.ok) throw new Error(`bets ${r.status}`);
        return r.json() as Promise<SimulationBetRow[]>;
      }),
    ])
      .then(([s, b]) => {
        setSimStatus(s);
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
    if (!simStatus?.assets?.length) return;
    const filtered = selected.filter((c) => availableSet.has(c));
    if (filtered.length === 0) {
      onConfigChange({ coins: availableCoins });
    } else if (filtered.length !== selected.length) {
      onConfigChange({ coins: filtered });
    }
  }, [simStatus?.assets, availableCoins, availableSet, selected, onConfigChange]);

  useEffect(() => {
    return subscribe("*", (msg: FeedMsg) => {
      if (msg.type === "simulation_bet_open") {
        const m = msg as SimulationBetOpenMsg;
        if (!selectedSet.has(m.asset)) return;
        const row: SimulationBetRow = {
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
        };
        setRows((prev) => [row, ...prev.filter((r) => r.id !== row.id)].slice(0, MAX_ROWS));
        refresh();
      } else if (msg.type === "simulation_bet_settle") {
        const m = msg as SimulationBetSettleMsg;
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
        msg.type === "simulation_signal" ||
        msg.type === "simulation_cycle_closed"
      ) {
        refresh();
      }
    });
  }, [subscribe, refresh, selectedSet]);

  const toggleCoin = (coin: SimCoin) => {
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
    () => aggregateAssetStats(selected, activeSides, activeLegs, simStatus?.by_asset_side_leg),
    [selected, activeSides, activeLegs, simStatus?.by_asset_side_leg]
  );

  const pnl = agg.total_pnl_usd;
  const pnlClass = pnl >= 0 ? styles.pnlPos : styles.pnlNeg;

  return (
    <div className={styles.root}>
      <div className={`${styles.toolbar} simulationToolbar`}>
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
        <span className={styles.simBadge}>SIM</span>
        {simStatus && (
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
              ? `Cannot load sim (${loadError}). Run frontend on :3000 with backend :8000.`
              : `Waiting for ${formatPairs(selected)} 15m liq ≥ threshold (long→UP, short→DN)…`}
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
                <span
                  className={styles.timeBlock}
                  title={
                    r.leg > 1
                      ? `${betTimeTooltip(r)} · leg ${r.leg}`
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
