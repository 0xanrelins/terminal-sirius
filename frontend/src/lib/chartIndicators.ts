import type { CandlestickData, LineData, UTCTimestamp } from "lightweight-charts";

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
