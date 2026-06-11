import type { CandlestickData, LineData, UTCTimestamp } from "lightweight-charts";
import { sessionBucketOpen } from "./barTime";

export type OhlcvBar = {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** EMA with leading whitespace until the period is filled. */
export function calculateEMA(
  candles: CandlestickData<UTCTimestamp>[],
  period: number
): LineData<UTCTimestamp>[] {
  const out: LineData<UTCTimestamp>[] = [];
  const mult = 2 / (period + 1);
  let ema: number | null = null;

  for (let i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      out.push({ time: candles[i].time });
    } else if (ema === null) {
      let sum = 0;
      for (let j = 0; j < period; j++) sum += candles[j].close;
      ema = sum / period;
      out.push({ time: candles[i].time, value: ema });
    } else {
      ema = (candles[i].close - ema) * mult + ema;
      out.push({ time: candles[i].time, value: ema });
    }
  }
  return out;
}

/** Rolling VWAP with leading whitespace until the period is filled. */
export function calculateVWAP(
  bars: OhlcvBar[],
  period: number
): LineData<UTCTimestamp>[] {
  const out: LineData<UTCTimestamp>[] = [];
  const p = Math.max(1, Math.floor(period));

  for (let i = 0; i < bars.length; i++) {
    if (i < p - 1) {
      out.push({ time: bars[i].time });
      continue;
    }
    const start = i - p + 1;
    let sumPv = 0;
    let sumV = 0;
    for (let j = start; j <= i; j++) {
      const b = bars[j];
      const vol = b.volume;
      if (vol <= 0) continue;
      const tp = (b.high + b.low + b.close) / 3;
      sumPv += tp * vol;
      sumV += vol;
    }
    if (sumV > 0) {
      out.push({ time: bars[i].time, value: sumPv / sumV });
    } else {
      out.push({ time: bars[i].time });
    }
  }
  return out;
}

/** Anchored VWAP: UTC session buckets (period × chart interval), one point per bar. */
export function calculateSessionVWAP(
  bars: OhlcvBar[],
  period: number,
  chartInterval: string
): LineData<UTCTimestamp>[] {
  return calculateSessionVWAPSegments(bars, period, chartInterval).flat();
}

/**
 * Same as session VWAP but one LineSeries per session so segments never connect across boundaries.
 */
export function calculateSessionVWAPSegments(
  bars: OhlcvBar[],
  period: number,
  chartInterval: string
): LineData<UTCTimestamp>[][] {
  const p = Math.max(1, Math.floor(period));
  const segments: LineData<UTCTimestamp>[][] = [];
  let current: LineData<UTCTimestamp>[] = [];

  let sumPv = 0;
  let sumV = 0;
  let currentBucket: number | null = null;

  const flush = () => {
    if (current.length > 0) {
      segments.push(current);
      current = [];
    }
  };

  for (const b of bars) {
    const bucket = sessionBucketOpen(b.time as number, chartInterval, p);
    if (currentBucket !== null && bucket !== currentBucket) {
      flush();
      sumPv = 0;
      sumV = 0;
    }
    currentBucket = bucket;

    const vol = b.volume;
    if (vol > 0) {
      const tp = (b.high + b.low + b.close) / 3;
      sumPv += tp * vol;
      sumV += vol;
    }
    if (sumV > 0) {
      current.push({ time: b.time, value: sumPv / sumV });
    }
  }
  flush();
  return segments;
}

/** Current session bucket VWAP at the latest bar — for live forming-bar tail updates. */
export function calculateSessionVwapTailPoint(
  bars: OhlcvBar[],
  period: number,
  chartInterval: string
): LineData<UTCTimestamp> | null {
  if (bars.length === 0) return null;

  const p = Math.max(1, Math.floor(period));
  const last = bars[bars.length - 1];
  const targetBucket = sessionBucketOpen(last.time as number, chartInterval, p);

  let sumPv = 0;
  let sumV = 0;
  for (const b of bars) {
    const bucket = sessionBucketOpen(b.time as number, chartInterval, p);
    if (bucket !== targetBucket) continue;
    const vol = b.volume;
    if (vol <= 0) continue;
    const tp = (b.high + b.low + b.close) / 3;
    sumPv += tp * vol;
    sumV += vol;
  }

  if (sumV <= 0) return null;
  return { time: last.time, value: sumPv / sumV };
}

export function isAnchoredVwapType(type: string): boolean {
  return type === "vwap" || type === "session_vwap";
}
