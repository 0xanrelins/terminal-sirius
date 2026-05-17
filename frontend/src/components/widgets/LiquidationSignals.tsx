import { useCallback, useEffect, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type { LiquidationMsg, LiquidationSignalRow } from "../../types";
import styles from "./LiquidationSignals.module.css";

const MAX_ROWS = 200;
const PERSIST_DEBOUNCE_MS = 400;
export const DEFAULT_MIN_NOTIONAL = 100_000;
/** v2: majors-only ingest; store all sizes; display threshold-filtered. */
export const LIQ_HISTORY_VERSION = 2;

export const LIQ_MAJOR_COINS = ["BTC", "ETH", "SOL", "DOGE", "XRP"] as const;
export type LiqMajorCoin = (typeof LIQ_MAJOR_COINS)[number];
export const DEFAULT_LIQ_COINS: LiqMajorCoin[] = [...LIQ_MAJOR_COINS];

const MAJOR_SYMBOLS = new Set<string>(LIQ_MAJOR_COINS);

const ASSET_TO_FEED_SYMBOL: Record<LiqMajorCoin, string> = {
  BTC: "BTCUSDT-PERP.BINANCE",
  ETH: "ETHUSDT-PERP.BINANCE",
  SOL: "SOLUSDT-PERP.BINANCE",
  DOGE: "DOGEUSDT-PERP.BINANCE",
  XRP: "XRPUSDT-PERP.BINANCE",
};

const MAJOR_COLORS: Record<string, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  SOL: "#14f195",
  DOGE: "#c2a633",
  XRP: "#38bdf8",
};

type Props = {
  minNotional: number;
  coins: LiqMajorCoin[];
  history: LiquidationSignalRow[];
  historyVersion?: number;
  onConfigChange: (patch: {
    minNotional?: number;
    coins?: LiqMajorCoin[];
    history?: LiquidationSignalRow[];
    historyVersion?: number;
  }) => void;
};

export function normalizeLiqCoins(coins: unknown): LiqMajorCoin[] {
  if (!Array.isArray(coins)) return DEFAULT_LIQ_COINS;
  const picked = coins.filter(
    (c): c is LiqMajorCoin => typeof c === "string" && MAJOR_SYMBOLS.has(c)
  );
  return picked.length > 0 ? picked : DEFAULT_LIQ_COINS;
}

function isValidRow(row: unknown): row is LiquidationSignalRow {
  if (!row || typeof row !== "object") return false;
  const r = row as LiquidationSignalRow;
  return (
    typeof r.id === "string" &&
    typeof r.symbol === "string" &&
    MAJOR_SYMBOLS.has(r.symbol) &&
    (r.side === "LONG" || r.side === "SHORT") &&
    typeof r.notional === "number" &&
    Number.isFinite(r.notional) &&
    typeof r.time === "number"
  );
}

function loadHistory(history: unknown, version?: number): LiquidationSignalRow[] {
  if (version !== LIQ_HISTORY_VERSION) return [];
  if (!Array.isArray(history)) return [];
  return history.filter(isValidRow).slice(0, MAX_ROWS);
}

function initRowSeq(history: LiquidationSignalRow[]): number {
  let max = 0;
  for (const row of history) {
    const m = /^liq-(\d+)$/.exec(row.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return max;
}

export function LiquidationSignals({
  minNotional,
  coins,
  history,
  historyVersion,
  onConfigChange,
}: Props) {
  const selected = normalizeLiqCoins(coins);
  const selectedSet = new Set<string>(selected);
  const { subscribe, status } = useFeed();
  const [rows, setRows] = useState<LiquidationSignalRow[]>(() =>
    loadHistory(history, historyVersion)
  );
  const [draftThreshold, setDraftThreshold] = useState(String(minNotional));
  const thresholdRef = useRef(minNotional);
  const rowSeqRef = useRef(initRowSeq(loadHistory(history, historyVersion)));
  const onConfigChangeRef = useRef(onConfigChange);
  onConfigChangeRef.current = onConfigChange;
  const skipPersistRef = useRef(true);
  const lastPersistedRef = useRef("");

  useEffect(() => {
    thresholdRef.current = minNotional;
    setDraftThreshold(String(minNotional));
  }, [minNotional]);

  useEffect(() => {
    if (historyVersion === LIQ_HISTORY_VERSION) return;
    setRows([]);
    rowSeqRef.current = 0;
    skipPersistRef.current = true;
    lastPersistedRef.current = "";
    onConfigChangeRef.current({
      history: [],
      historyVersion: LIQ_HISTORY_VERSION,
    });
  }, [historyVersion]);

  useEffect(() => {
    if (skipPersistRef.current) {
      skipPersistRef.current = false;
      return;
    }
    const payload = { history: rows, historyVersion: LIQ_HISTORY_VERSION };
    const key = JSON.stringify(payload);
    if (key === lastPersistedRef.current) return;

    const t = setTimeout(() => {
      lastPersistedRef.current = key;
      onConfigChangeRef.current(payload);
    }, PERSIST_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [rows]);

  const pushRow = useCallback((liq: LiquidationMsg) => {
    const asset = displaySymbol(liq.symbol);
    if (!selected.includes(asset as LiqMajorCoin)) return;

    const row: LiquidationSignalRow = {
      id: `liq-${++rowSeqRef.current}`,
      symbol: asset,
      side: liq.side === "SELL" ? "LONG" : "SHORT",
      notional: liq.notional,
      time: liq.time,
    };

    setRows((prev) => [row, ...prev].slice(0, MAX_ROWS));
  }, [selected]);

  useEffect(() => {
    const unsubs = selected.map((asset) =>
      subscribe(ASSET_TO_FEED_SYMBOL[asset], (msg) => {
        if (msg.type === "liquidation") pushRow(msg);
      })
    );
    return () => unsubs.forEach((u) => u());
  }, [subscribe, pushRow, selected]);

  const commitThreshold = () => {
    const next = Math.max(0, Number(draftThreshold.replace(/,/g, "")) || 0);
    thresholdRef.current = next;
    setDraftThreshold(String(next));
    onConfigChange({ minNotional: next });
  };

  const visibleRows = rows.filter(
    (r) => selectedSet.has(r.symbol) && r.notional >= minNotional
  );

  const toggleCoin = (coin: LiqMajorCoin) => {
    const next = selectedSet.has(coin)
      ? selected.filter((c) => c !== coin)
      : [...selected, coin];
    if (next.length === 0) return;
    onConfigChange({ coins: next });
  };

  return (
    <div className={styles.root}>
      <div className={`${styles.toolbar} signalsToolbar`}>
        <div className={styles.coins}>
          {LIQ_MAJOR_COINS.map((coin) => {
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
        {visibleRows.length === 0 ? (
          <p className={styles.empty}>
            Waiting for {formatPairs(selected)} liquidations ≥ {formatNotional(minNotional)}…
          </p>
        ) : (
          visibleRows.map((row) => {
            const accent = MAJOR_COLORS[row.symbol];
            return (
              <div
                key={row.id}
                className={`${styles.row} ${styles.rowMajor}`}
                style={{ borderLeftColor: accent }}
              >
                <span className={`${styles.sym} ${styles.symMajor}`} style={{ color: accent }}>
                  {row.symbol}
                </span>
                <span className={row.side === "LONG" ? styles.long : styles.short}>
                  {row.side}
                </span>
                <span className={`${styles.notional} ${styles.notionalMajor}`}>
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

function formatPairs(coins: readonly string[]): string {
  return coins.join("/");
}
