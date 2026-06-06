import { useEffect, useMemo, useState } from "react";
import { useFeed } from "../../context/FeedContext";
import type {
  StrategySignalSnapshotMsg,
  StrategySignalSymbolState,
} from "../../types";
import styles from "./StrategySignalsWidget.module.css";

export const STRATEGY_BINANCE_SYMBOLS = [
  "BTCUSDT-PERP.BINANCE",
  "ETHUSDT-PERP.BINANCE",
  "SOLUSDT-PERP.BINANCE",
  "XRPUSDT-PERP.BINANCE",
  "DOGEUSDT-PERP.BINANCE",
] as const;

const STALE_MS = 8000;

type Props = {
  symbols?: string[];
  onConfigChange: (patch: { symbols?: string[] }) => void;
};

function shortSymbol(symbol: string): string {
  return symbol.split("USDT")[0] || symbol;
}

function fmtNum(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function fmtBool(v: boolean): string {
  return v ? "yes" : "no";
}

function boolClass(v: boolean): string {
  return v ? styles.rowValOn : styles.rowValOff;
}

function decisionClass(decision: StrategySignalSymbolState["decision"]): string {
  if (decision === "LONG") return `${styles.decision} ${styles.decisionLong}`;
  if (decision === "SHORT") return `${styles.decision} ${styles.decisionShort}`;
  return `${styles.decision} ${styles.decisionHold}`;
}

function normalizeSelected(symbols: string[] | undefined): string[] {
  const allowed = new Set<string>(STRATEGY_BINANCE_SYMBOLS);
  const src = symbols?.length ? symbols : [...STRATEGY_BINANCE_SYMBOLS];
  return src.filter((s) => allowed.has(s));
}

function Row({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <>
      <div className={styles.rowKey}>{label}</div>
      <div className={`${styles.rowVal} ${valueClass ?? ""}`}>{value}</div>
    </>
  );
}

function SymbolBlock({ symbol, state }: { symbol: string; state: StrategySignalSymbolState }) {
  if (!state.vwap_ready) {
    return (
      <div className={styles.block}>
        <div className={styles.blockHeader}>
          <span className={styles.symbol}>{shortSymbol(symbol)}</span>
          <span className={decisionClass("HOLD")}>WARMING</span>
        </div>
        <div className={styles.warmup}>Warming up (~15m bar window)…</div>
      </div>
    );
  }

  return (
    <div className={styles.block}>
      <div className={styles.blockHeader}>
        <span className={styles.symbol}>{shortSymbol(symbol)}</span>
        <span className={decisionClass(state.decision)}>{state.decision}</span>
      </div>

      <div className={styles.group}>
        <div className={styles.groupTitle}>VWAP</div>
        <div className={styles.rows}>
          <Row label="vwap" value={fmtNum(state.vwap)} />
          <Row label="slope" value={fmtNum(state.slope, 6)} />
          <Row label="low_zone" value={fmtNum(state.low_zone)} />
          <Row label="high_zone" value={fmtNum(state.high_zone)} />
          <Row label="close" value={fmtNum(state.close)} />
        </div>
      </div>

      <div className={styles.group}>
        <div className={styles.groupTitle}>Liq</div>
        <div className={styles.rows}>
          <Row label="long_vol" value={fmtUsd(state.long_volume)} />
          <Row label="short_vol" value={fmtUsd(state.short_volume)} />
          <Row label="threshold" value={fmtUsd(state.liq_threshold)} />
          <Row
            label="long_hit"
            value={fmtBool(state.liq_long_hit)}
            valueClass={boolClass(state.liq_long_hit)}
          />
          <Row
            label="short_hit"
            value={fmtBool(state.liq_short_hit)}
            valueClass={boolClass(state.liq_short_hit)}
          />
          <Row
            label="long_trig"
            value={fmtBool(state.liq_long_trigger)}
            valueClass={boolClass(state.liq_long_trigger)}
          />
          <Row
            label="short_trig"
            value={fmtBool(state.liq_short_trigger)}
            valueClass={boolClass(state.liq_short_trigger)}
          />
        </div>
      </div>

      <div className={styles.group}>
        <div className={styles.groupTitle}>Signal</div>
        <div className={styles.rows}>
          <Row
            label="in_range"
            value={fmtBool(state.in_range)}
            valueClass={boolClass(state.in_range)}
          />
          <Row
            label="long_zone"
            value={fmtBool(state.long_zone)}
            valueClass={boolClass(state.long_zone)}
          />
          <Row
            label="short_zone"
            value={fmtBool(state.short_zone)}
            valueClass={boolClass(state.short_zone)}
          />
        </div>
      </div>
    </div>
  );
}

export function StrategySignalsWidget({ symbols, onConfigChange }: Props) {
  const { subscribe, status } = useFeed();
  const [snapshot, setSnapshot] = useState<StrategySignalSnapshotMsg | null>(null);
  const [lastMs, setLastMs] = useState(0);

  const selected = useMemo(() => normalizeSelected(symbols), [symbols]);

  useEffect(() => {
    return subscribe("*", (msg) => {
      if (msg.type === "strategy_signal_snapshot") {
        setSnapshot(msg as StrategySignalSnapshotMsg);
        setLastMs(Date.now());
      }
    });
  }, [subscribe]);

  const stale = lastMs > 0 && Date.now() - lastMs > STALE_MS;
  const live = lastMs > 0 && !stale;

  function toggleSymbol(sym: string) {
    const next = selected.includes(sym)
      ? selected.filter((s) => s !== sym)
      : [...selected, sym];
    onConfigChange({ symbols: next.length ? next : [...STRATEGY_BINANCE_SYMBOLS] });
  }

  const visible = selected.filter((sym) => snapshot?.symbols[sym]);

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <span className={styles.title}>STRATEGY SIGNALS</span>
        <span className={styles.statusPill}>
          <span
            className={`${styles.dot} ${
              status !== "open" ? styles.dotRed : live ? styles.dotGreen : stale ? styles.dotAmber : styles.dotRed
            }`}
          />
          {status !== "open" ? "WS" : live ? "LIVE" : stale ? "STALE" : "WAIT"}
        </span>
      </header>

      <div className={styles.filters}>
        {STRATEGY_BINANCE_SYMBOLS.map((sym) => (
          <button
            key={sym}
            type="button"
            className={`${styles.chip} ${selected.includes(sym) ? styles.chipActive : ""}`}
            onClick={() => toggleSymbol(sym)}
          >
            {shortSymbol(sym)}
          </button>
        ))}
      </div>

      <div className={styles.body}>
        {!snapshot && (
          <div className={styles.empty}>Waiting for strategy_signal_snapshot…</div>
        )}
        {snapshot &&
          selected.map((sym) => {
            const st = snapshot.symbols[sym];
            if (!st) {
              return (
                <div key={sym} className={styles.block}>
                  <div className={styles.blockHeader}>
                    <span className={styles.symbol}>{shortSymbol(sym)}</span>
                  </div>
                  <div className={styles.warmup}>No data yet</div>
                </div>
              );
            }
            return <SymbolBlock key={sym} symbol={sym} state={st} />;
          })}
        {snapshot && selected.length > 0 && visible.length === 0 && (
          <div className={styles.empty}>No matching symbol data in snapshot</div>
        )}
      </div>
    </div>
  );
}
