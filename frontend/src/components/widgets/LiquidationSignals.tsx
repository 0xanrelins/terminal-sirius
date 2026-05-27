import { useCallback, useEffect, useRef, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import { format15mBarWindow, formatWallTimeSec } from "../../lib/betTiming";
import type { LiquidationMsg, LiquidationSignalRow } from "../../types";
import styles from "./LiquidationSignals.module.css";

const MAX_ROWS = 200;
export const DEFAULT_MIN_NOTIONAL = 100_000;
/** History from liquidation_watchlist_events (backend stream persist). */
export const LIQ_HISTORY_VERSION = 4;

export const LIQ_MAJOR_COINS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "HYPE", "BNB"] as const;
export type LiqMajorCoin = (typeof LIQ_MAJOR_COINS)[number];
export const DEFAULT_LIQ_COINS: LiqMajorCoin[] = [...LIQ_MAJOR_COINS];

const MAJOR_SYMBOLS = new Set<string>(LIQ_MAJOR_COINS);

const ASSET_TO_FEED_SYMBOL: Record<LiqMajorCoin, string> = {
  BTC: "BTCUSDT-PERP.BINANCE",
  ETH: "ETHUSDT-PERP.BINANCE",
  SOL: "SOLUSDT-PERP.BINANCE",
  DOGE: "DOGEUSDT-PERP.BINANCE",
  XRP: "XRPUSDT-PERP.BINANCE",
  HYPE: "HYPEUSDT-PERP.BINANCE",
  BNB: "BNBUSDT-PERP.BINANCE",
};

const MAJOR_COLORS: Record<string, string> = {
  BTC: "#f7931a",
  ETH: "#627eea",
  SOL: "#14f195",
  DOGE: "#c2a633",
  XRP: "#38bdf8",
  HYPE: "#7c3aed",
  BNB: "#f0b90b",
};

type Props = {
  minNotional: number;
  coins: LiqMajorCoin[];
  historyVersion?: number;
  onConfigChange: (patch: {
    minNotional?: number;
    coins?: LiqMajorCoin[];
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

function displaySymbol(symbol: string): string {
  return symbol.replace("-PERP.BINANCE", "").replace("USDT", "");
}

function msgToRow(liq: LiquidationMsg): LiquidationSignalRow | null {
  const asset = displaySymbol(liq.symbol);
  if (!MAJOR_SYMBOLS.has(asset)) return null;
  const id =
    liq.trade_id != null
      ? `liq-${liq.trade_id}`
      : `liq-${liq.time}-${liq.side}-${Math.round(liq.notional)}`;
  return {
    id,
    symbol: asset,
    side: liq.side === "SELL" ? "LONG" : "SHORT",
    notional: liq.notional,
    time: liq.time,
  };
}

function mergeRows(
  prev: LiquidationSignalRow[],
  incoming: LiquidationSignalRow[]
): LiquidationSignalRow[] {
  const seen = new Set<string>();
  const out: LiquidationSignalRow[] = [];
  for (const row of [...incoming, ...prev]) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    out.push(row);
    if (out.length >= MAX_ROWS) break;
  }
  return out;
}

export function LiquidationSignals({
  minNotional,
  coins,
  historyVersion,
  onConfigChange,
}: Props) {
  const selected = normalizeLiqCoins(coins);
  const selectedSet = new Set<string>(selected);
  const { subscribe, status } = useFeed();
  const [rows, setRows] = useState<LiquidationSignalRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [draftThreshold, setDraftThreshold] = useState(String(minNotional));
  const onConfigChangeRef = useRef(onConfigChange);
  onConfigChangeRef.current = onConfigChange;

  useEffect(() => {
    setDraftThreshold(String(minNotional));
  }, [minNotional]);

  useEffect(() => {
    if (historyVersion === LIQ_HISTORY_VERSION) return;
    onConfigChangeRef.current({ historyVersion: LIQ_HISTORY_VERSION });
  }, [historyVersion]);

  const symbolsParam = selected.map((c) => ASSET_TO_FEED_SYMBOL[c]).join(",");

  useEffect(() => {
    let cancelled = false;
    setRows([]);
    setHistoryLoading(true);

    const params = new URLSearchParams({
      symbols: symbolsParam,
      limit: String(MAX_ROWS),
    });
    fetch(`/liquidation-events?${params}`)
      .then((r) => {
        if (!r.ok) throw new Error(`liquidation-events ${r.status}`);
        return r.json() as Promise<LiquidationMsg[]>;
      })
      .then((events) => {
        if (cancelled) return;
        const incoming = events
          .map((e) => msgToRow({ ...e, type: "liquidation" }))
          .filter((r): r is LiquidationSignalRow => r !== null);
        setRows((prev) => mergeRows(prev, incoming));
      })
      .catch(() => {
        /* backend may be offline — live WS still works */
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [symbolsParam]);

  const pushRow = useCallback((liq: LiquidationMsg) => {
    const row = msgToRow(liq);
    if (!row || !selected.includes(row.symbol as LiqMajorCoin)) return;
    setRows((prev) => mergeRows(prev, [row]));
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
        {historyLoading && visibleRows.length === 0 ? (
          <p className={styles.empty}>Loading history…</p>
        ) : visibleRows.length === 0 ? (
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
                <span className={styles.time}>
                  <span className={styles.timeEvent}>{formatWallTimeSec(row.time)}</span>
                  <span className={styles.timeSep}>·</span>
                  <span className={styles.timeBar15} title="15m liquidation bar (open–close)">
                    {format15mBarWindow(row.time)}
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

function formatNotional(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

function formatPairs(coins: readonly string[]): string {
  return coins.join("/");
}
