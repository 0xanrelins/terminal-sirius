import type { UTCTimestamp } from "lightweight-charts";
import type { LiquidationBar, LiquidationBarSnapshot } from "../types";

const LONG_LIQ_COLOR = "#ef4444";
const SHORT_LIQ_COLOR = "#22c55e";
const LIQ_MUTED_COLOR = "#4a4a55";

/** Backend liquidation buckets start at 5s (no 1s bars). */
export function liqApiInterval(chartInterval: string): string {
  return chartInterval === "1s" ? "5s" : chartInterval;
}

export function liquidationBarForChart(
  bars: LiquidationBarSnapshot[] | undefined,
  chartInterval: string
): LiquidationBarSnapshot | undefined {
  if (!bars?.length) return undefined;
  const direct = bars.find((b) => b.interval === chartInterval);
  if (direct) return direct;
  if (chartInterval === "1s") {
    return bars.find((b) => b.interval === "5s");
  }
  return undefined;
}

/** Match sim/live: highlight only when long or short alone ≥ threshold (not combined). */
export function liqHistColor(
  long: number,
  short: number,
  threshold: number
): string {
  const longHit = long >= threshold;
  const shortHit = short >= threshold;
  if (!longHit && !shortHit) return LIQ_MUTED_COLOR;
  if (longHit && shortHit) return long >= short ? LONG_LIQ_COLOR : SHORT_LIQ_COLOR;
  if (longHit) return LONG_LIQ_COLOR;
  return SHORT_LIQ_COLOR;
}

export function liqHistValue(
  long: number,
  short: number,
  threshold: number
): number {
  const longHit = long >= threshold;
  const shortHit = short >= threshold;
  if (longHit && shortHit) return Math.max(long, short);
  if (longHit) return long;
  if (shortHit) return short;
  return long >= short ? long : short;
}

export function liqHistogramPoint(
  time: number,
  long: number,
  short: number,
  threshold: number
): { time: UTCTimestamp; value: number; color: string } {
  return {
    time: time as UTCTimestamp,
    value: liqHistValue(long, short, threshold),
    color: liqHistColor(long, short, threshold),
  };
}

export function liqToHistogramData(bars: LiquidationBar[], threshold: number) {
  return bars.map((b) => liqHistogramPoint(b.time, b.long, b.short, threshold));
}
