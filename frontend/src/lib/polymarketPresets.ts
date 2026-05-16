/** Rolling 15m up/down markets — must match backend PRESET_15M_SERIES. */
export const POLYMARKET_15M_PRESETS = [
  { series: "btc-updown-15m", label: "BTC", asset: "BTC" },
  { series: "eth-updown-15m", label: "ETH", asset: "ETH" },
  { series: "sol-updown-15m", label: "SOL", asset: "SOL" },
  { series: "doge-updown-15m", label: "DOGE", asset: "DOGE" },
  { series: "xrp-updown-15m", label: "XRP", asset: "XRP" },
] as const;

export function seriesToSymbol(series: string): string {
  return `${series}.POLYMARKET`;
}

export type PolymarketPreset = {
  series: string;
  label: string;
  asset: string;
  symbol: string;
  current_slug: string;
  yes_price: number | null;
  question: string | null;
};
