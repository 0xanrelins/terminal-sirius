import { useCallback, useEffect, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type { LiquidationMsg, LiquidationSignalRow } from "../../types";
import styles from "./LiquidationSignals.module.css";

const MAX_ROWS = 200;
const PERSIST_DEBOUNCE_MS = 400;
export const DEFAULT_MIN_NOTIONAL = 100_000;

const MAJOR_SYMBOLS = new Set(["BTC", "ETH", "SOL", "DOGE", "XRP"]);

const MAJOR_COLORS: Record<string, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  SOL: "#14f195",
  DOGE: "#c2a633",
  XRP: "#38bdf8",
};

type Props = {
  minNotional: number;
  history: LiquidationSignalRow[];
  onConfigChange: (patch: {
    minNotional?: number;
    history?: LiquidationSignalRow[];
  }) => void;
};

function initRowSeq(history: LiquidationSignalRow[]): number {
  let max = 0;
  for (const row of history) {
    const m = /^liq-(\d+)$/.exec(row.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max;
}

export function LiquidationSignals({ minNotional, history, onConfigChange }: Props) {
  const { subscribe, status } = useFeed();
  const [rows, setRows] = useState<LiquidationSignalRow[]>(() =>
    history.slice(0, MAX_ROWS)
  );
  const [draftThreshold, setDraftThreshold] = useState(String(minNotional));
  const thresholdRef = useRef(minNotional);
  const rowSeqRef = useRef(initRowSeq(history));
  const onConfigChangeRef = useRef(onConfigChange);
  onConfigChangeRef.current = onConfigChange;

  useEffect(() => {
    thresholdRef.current = minNotional;
    setDraftThreshold(String(minNotional));
  }, [minNotional]);

  useEffect(() => {
    const t = setTimeout(() => {
      onConfigChangeRef.current({ history: rows });
    }, PERSIST_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [rows]);

  const pushRow = useCallback((liq: LiquidationMsg) => {
    if (liq.notional < thresholdRef.current) return;

    const row: LiquidationSignalRow = {
      id: `liq-${++rowSeqRef.current}`,
      symbol: displaySymbol(liq.symbol),
      side: liq.side === "SELL" ? "LONG" : "SHORT",
      notional: liq.notional,
      time: liq.time,
    };

    setRows((prev) => [row, ...prev].slice(0, MAX_ROWS));
  }, []);

  useEffect(() => {
    return subscribe("*", (msg) => {
      if (msg.type === "liquidation") pushRow(msg);
    });
  }, [subscribe, pushRow]);

  const commitThreshold = () => {
    const next = Math.max(0, Number(draftThreshold.replace(/,/g, "")) || 0);
    thresholdRef.current = next;
    setDraftThreshold(String(next));
    onConfigChange({ minNotional: next });
  };

  return (
    <div className={styles.root}>
      <div className={`${styles.toolbar} signalsToolbar`}>
        <span className={styles.title}>Liquidations</span>
        <label className={styles.threshold}>
          <span className={styles.thresholdLabel}>Min $</span>
          <input
            className={styles.thresholdInput}
            value={draftThreshold}
            onChange={(e) => setDraftThreshold(e.target.value)}
            onBlur={commitThreshold}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            inputMode="numeric"
            spellCheck={false}
          />
        </label>
        <span className={`${styles.status} ${styles[status]}`}>{status}</span>
      </div>

      <div className={styles.list}>
        {rows.length === 0 ? (
          <p className={styles.empty}>
            Waiting for liquidations ≥ {formatNotional(minNotional)}…
          </p>
        ) : (
          rows.map((row) => {
            const major = MAJOR_SYMBOLS.has(row.symbol);
            const accent = MAJOR_COLORS[row.symbol];
            return (
              <div
                key={row.id}
                className={`${styles.row} ${major ? styles.rowMajor : styles.rowAlt}`}
                style={major ? { borderLeftColor: accent } : undefined}
              >
                <span
                  className={`${styles.sym} ${major ? styles.symMajor : styles.symAlt}`}
                  style={major ? { color: accent } : undefined}
                >
                  {row.symbol}
                </span>
                <span className={row.side === "LONG" ? styles.long : styles.short}>
                  {row.side}
                </span>
                <span className={`${styles.notional} ${major ? styles.notionalMajor : ""}`}>
                  {formatNotional(row.notional)}
                </span>
                <span className={styles.time}>{formatTime(row.time)}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function displaySymbol(symbol: string): string {
  return symbol.replace("-PERP.BINANCE", "").replace("USDT", "");
}

function formatNotional(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false });
}
