const WINDOW_SEC = 900;

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
