import { useCallback, useEffect, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type {
  FeedMsg,
  SimulationBetOpenMsg,
  SimulationBetRow,
  SimulationBetSettleMsg,
  SimulationSide,
  SimulationStatus,
} from "../../types";
import styles from "./SimulationPanel.module.css";

const MAX_ROWS = 150;

function sideLabel(side: SimulationSide): string {
  return side === "long" ? "UP" : "DN";
}

function legLabel(side: SimulationSide, leg: number): string {
  return `${side === "long" ? "L" : "S"}${leg}`;
}

export function SimulationPanel() {
  const { subscribe, status } = useFeed();
  const [simStatus, setSimStatus] = useState<SimulationStatus | null>(null);
  const [rows, setRows] = useState<SimulationBetRow[]>([]);

  const refresh = useCallback(() => {
    fetch("/simulation/status")
      .then((r) => r.json())
      .then((s: SimulationStatus) => setSimStatus(s))
      .catch(() => {});
    fetch("/simulation/bets?limit=100")
      .then((r) => r.json())
      .then((b: SimulationBetRow[]) =>
        setRows(
          b.map((row) => ({ ...row, side: row.side ?? "long" })).slice(0, MAX_ROWS)
        )
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    return subscribe("*", (msg: FeedMsg) => {
      if (msg.type === "simulation_bet_open") {
        const m = msg as SimulationBetOpenMsg;
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
          settled_at: null,
          asset: m.asset,
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
  }, [subscribe, refresh]);

  const pnl = simStatus?.total_pnl_usd ?? 0;
  const pnlClass = pnl >= 0 ? styles.pnlPos : styles.pnlNeg;
  const longStats = simStatus?.by_side?.long;
  const shortStats = simStatus?.by_side?.short;

  return (
    <div className={styles.root}>
      <div className={`${styles.toolbar} simulationToolbar`}>
        <span className={styles.title}>Liq → Poly Sim</span>
        {simStatus && (
          <>
            <span className={styles.stat}>
              PnL <strong className={pnlClass}>${pnl.toFixed(2)}</strong>
            </span>
            <span className={styles.stat}>
              WR <strong>{simStatus.win_rate.toFixed(0)}%</strong>
            </span>
            <span className={styles.stat}>
              Open <strong>{simStatus.open_bets}</strong>
            </span>
            {longStats && (
              <span className={styles.stat}>
                UP <strong className={styles.sideUp}>{longStats.open_bets}</strong>
              </span>
            )}
            {shortStats && (
              <span className={styles.stat}>
                DN <strong className={styles.sideDn}>{shortStats.open_bets}</strong>
              </span>
            )}
          </>
        )}
        <span className={`${styles.status} ${styles[status]}`}>{status}</span>
      </div>

      <div className={styles.list}>
        {rows.length === 0 ? (
          <p className={styles.empty}>
            Waiting for 15m long/short liq bar signals…
          </p>
        ) : (
          rows.map((r) => {
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
                </span>
                <span
                  className={
                    r.outcome === "win"
                      ? styles.outcomeWin
                      : r.outcome === "loss"
                        ? styles.outcomeLoss
                        : styles.pending
                  }
                >
                  {r.outcome ?? "open"}
                </span>
                <span
                  className={
                    r.pnl_usd != null
                      ? r.pnl_usd >= 0
                        ? styles.outcomeWin
                        : styles.outcomeLoss
                      : styles.meta
                  }
                >
                  {r.pnl_usd != null
                    ? `${r.pnl_usd >= 0 ? "+" : ""}$${r.pnl_usd.toFixed(2)}`
                    : formatTime(r.opened_at)}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false });
}
