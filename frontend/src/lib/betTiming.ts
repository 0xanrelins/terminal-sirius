const WINDOW_SEC = 900;

export const BAR_15M_SEC = WINDOW_SEC;

/** Wall-clock time with seconds (liquidation widget). */
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

export function formatBarTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
