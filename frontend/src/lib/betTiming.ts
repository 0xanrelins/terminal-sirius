const WINDOW_SEC = 900;

export const BAR_15M_SEC = WINDOW_SEC;

/** Wall-clock time with seconds (liquidation widget / sim rows). */
export function formatWallTimeSec(tsUnixSec: number): string {
  return new Date(tsUnixSec * 1000).toLocaleTimeString("en-GB", {
    hour12: false,
  });
}

/** Binance-style 15m bucket open (unix sec), aligned with liquidation bars. */
export function bar15OpenSec(eventTsSec: number): number {
  return Math.floor(eventTsSec / WINDOW_SEC) * WINDOW_SEC;
}

/** Display "HH:mm–HH:mm" for the 15m bar containing eventTsSec. */
export function format15mBarWindow(eventTsSec: number): string {
  const open = bar15OpenSec(eventTsSec);
  const end = open + WINDOW_SEC;
  return `${formatBarTime(open)}–${formatBarTime(end)}`;
}

/** 15m liq bar open time for display (matches chart liquidation histogram). */
export function liqBarOpen(row: {
  liq_bar_open?: number | null;
  candle_open: number;
  leg: number;
}): number {
  if (row.liq_bar_open != null && row.liq_bar_open > 0) {
    return row.liq_bar_open;
  }
  // leg1 candle_open = liq bar + 15m; leg2 = liq bar + 30m
  const windowsAfterLiq = row.leg === 1 ? 1 : 2;
  return row.candle_open - WINDOW_SEC * windowsAfterLiq;
}

export function signalTimestamp(row: {
  signal_time?: number | null;
  opened_at: number;
}): number {
  return row.signal_time ?? row.opened_at;
}

/** Cycle liq threshold at signal time (e.g. ≥$200k). */
export function formatLiqThreshold(usd: number): string {
  if (usd >= 1_000_000) return `≥$${(usd / 1_000_000).toFixed(1)}M`;
  if (usd >= 1_000) return `≥$${Math.round(usd / 1_000)}k`;
  return `≥$${Math.round(usd)}`;
}

export function formatBarTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function betTimeTooltip(row: {
  liq_bar_open?: number | null;
  candle_open: number;
  leg: number;
  signal_time?: number | null;
  opened_at: number;
  poly_slug?: string;
}): string {
  const liq = formatBarTime(liqBarOpen(row));
  const sig = formatBarTime(signalTimestamp(row));
  const poly = formatBarTime(row.candle_open);
  const slug = row.poly_slug ? ` · ${row.poly_slug}` : "";
  return `Liq ${liq} · Signal ${sig} → Poly ${poly}${slug}`;
}
