import { POLYMARKET_15M_PRESETS, seriesToSymbol } from "./polymarketPresets";

/** Map Binance perp feed symbol → rolling 15m Polymarket series id. */
export function binancePerpToPolySeries(binanceSymbol: string): string | null {
  const base = binanceSymbol.replace("-PERP.BINANCE", "").replace("USDT", "");
  if (!base) return null;
  const preset = POLYMARKET_15M_PRESETS.find((p) => p.asset === base);
  return preset?.series ?? null;
}

export function polySeriesToFeedSymbol(series: string): string {
  return seriesToSymbol(series);
}
