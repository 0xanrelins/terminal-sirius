export const INTERVAL_SECONDS: Record<string, number> = {
  "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
  "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
  "1d": 86400,
};

/** Binance / Lightweight Charts bar key — bucket open time in seconds. */
export function barOpenTime(timeSec: number, interval: string): number {
  const barSec = INTERVAL_SECONDS[interval] ?? 60;
  return Math.floor(timeSec / barSec) * barSec;
}

export function currentBarBucket(interval: string): number {
  return barOpenTime(Math.floor(Date.now() / 1000), interval);
}

/** Seconds remaining until the open bar bucket closes (UTC-aligned). */
export function secondsUntilBucketEnd(
  interval: string,
  nowSec = Math.floor(Date.now() / 1000)
): number {
  const barSec = INTERVAL_SECONDS[interval] ?? 60;
  const bucketOpen = barOpenTime(nowSec, interval);
  return bucketOpen + barSec - nowSec;
}

/** UTC session bucket open for anchored VWAP (period bars × chart interval). */
export function sessionBucketOpen(
  barTimeSec: number,
  chartInterval: string,
  periodBars: number
): number {
  const barSec = INTERVAL_SECONDS[chartInterval] ?? 60;
  const sessionSec = Math.max(1, Math.floor(periodBars)) * barSec;
  return Math.floor(barTimeSec / sessionSec) * sessionSec;
}
