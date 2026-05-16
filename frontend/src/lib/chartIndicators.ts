import type { CandlestickData, LineData, UTCTimestamp } from "lightweight-charts";

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
