import type { ChartIndicator } from "../types";

export const CHART_SYMBOLS = [
  "BTCUSDT-PERP.BINANCE",
  "ETHUSDT-PERP.BINANCE",
  "SOLUSDT-PERP.BINANCE",
  "XRPUSDT-PERP.BINANCE",
  "DOGEUSDT-PERP.BINANCE",
  "HYPEUSDT-PERP.BINANCE",
] as const;

export const CHART_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"] as const;

export type IndicatorPreset =
  | { label: string; type: "ema"; period: number }
  | { label: string; type: "liquidations" };

export const INDICATOR_PRESETS: IndicatorPreset[] = [
  { label: "EMA 7", type: "ema", period: 7 },
  { label: "EMA 20", type: "ema", period: 20 },
  { label: "EMA 50", type: "ema", period: 50 },
  { label: "EMA 200", type: "ema", period: 200 },
  { label: "Liquidations", type: "liquidations" },
];

const MA_COLORS = ["#2962FF", "#f59e0b", "#a78bfa", "#22d3ee", "#f472b6"];

export function symbolShort(symbol: string): string {
  return symbol.replace("-PERP.BINANCE", "");
}

export function presetId(preset: IndicatorPreset): string {
  if (preset.type === "liquidations") return "liquidations";
  return `ema-${preset.period}`;
}

export function isPresetActive(indicators: ChartIndicator[], preset: IndicatorPreset): boolean {
  return indicators.some((i) => i.id === presetId(preset));
}

export function indicatorLabel(ind: ChartIndicator): string {
  if (ind.type === "liquidations") return "Liquidations";
  return `EMA ${ind.period}`;
}

export function maColor(index: number): string {
  return MA_COLORS[index % MA_COLORS.length];
}
