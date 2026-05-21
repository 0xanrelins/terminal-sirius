import type { ChartIndicator } from "../types";

export const CHART_SYMBOLS = [
  "BTCUSDT-PERP.BINANCE",
  "ETHUSDT-PERP.BINANCE",
  "SOLUSDT-PERP.BINANCE",
  "XRPUSDT-PERP.BINANCE",
  "DOGEUSDT-PERP.BINANCE",
  "HYPEUSDT-PERP.BINANCE",
  "BNBUSDT-PERP.BINANCE",
] as const;

/** Binance perps used in the multi-series comparison widget. */
export const COMPARISON_SYMBOLS = [
  "BTCUSDT-PERP.BINANCE",
  "ETHUSDT-PERP.BINANCE",
  "SOLUSDT-PERP.BINANCE",
  "DOGEUSDT-PERP.BINANCE",
  "XRPUSDT-PERP.BINANCE",
] as const;

export const COMPARISON_COLORS: Record<(typeof COMPARISON_SYMBOLS)[number], string> = {
  "BTCUSDT-PERP.BINANCE": "#f7931a",
  "ETHUSDT-PERP.BINANCE": "#627eea",
  "SOLUSDT-PERP.BINANCE": "#14f195",
  "DOGEUSDT-PERP.BINANCE": "#c2a633",
  "XRPUSDT-PERP.BINANCE": "#38bdf8",
};

export type ComparisonSymbol = (typeof COMPARISON_SYMBOLS)[number];

const COMPARISON_SYMBOL_SET = new Set<string>(COMPARISON_SYMBOLS);

export const DEFAULT_COMPARISON_SYMBOLS: ComparisonSymbol[] = [...COMPARISON_SYMBOLS];

export function normalizeComparisonSymbols(symbols: unknown): ComparisonSymbol[] {
  if (!Array.isArray(symbols)) return DEFAULT_COMPARISON_SYMBOLS;
  const picked = symbols.filter(
    (s): s is ComparisonSymbol => typeof s === "string" && COMPARISON_SYMBOL_SET.has(s)
  );
  return picked.length > 0 ? picked : DEFAULT_COMPARISON_SYMBOLS;
}

export const CHART_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"] as const;

/** Default candles on first chart open (matches legacy INITIAL_LIMIT). */
export const DEFAULT_CANDLESTICK_BARS = 500;
export const MAX_CANDLESTICK_BARS = 1000;
export const CANDLESTICK_BAR_PRESETS = [50, 100, 200, 500, 1000] as const;

export function clampInitialBars(value?: number): number {
  const n = value ?? DEFAULT_CANDLESTICK_BARS;
  return Math.min(MAX_CANDLESTICK_BARS, Math.max(10, Math.floor(n)));
}

export type IndicatorPreset =
  | { label: string; type: "ema"; period: number }
  | { label: string; type: "vwap"; period: number }
  | { label: string; type: "rolling_vwap"; period: number }
  | { label: string; type: "liquidations" };

export const DEFAULT_EMA_PERIOD = 20;
export const DEFAULT_VWAP_PERIOD = 20;
export const DEFAULT_ROLLING_VWAP_PERIOD = 20;

/** Min total liquidation notional (USD) per bar to highlight candles. */
export const DEFAULT_LIQ_THRESHOLD = 50_000;

export const INDICATOR_PRESETS: IndicatorPreset[] = [
  { label: "EMA", type: "ema", period: DEFAULT_EMA_PERIOD },
  { label: "VWAP", type: "vwap", period: DEFAULT_VWAP_PERIOD },
  { label: "Rolling VWAP", type: "rolling_vwap", period: DEFAULT_ROLLING_VWAP_PERIOD },
  { label: "Liquidations", type: "liquidations" },
];

const MA_COLORS = ["#2962FF", "#f59e0b", "#a78bfa", "#22d3ee", "#f472b6"];

export const INDICATOR_LINE_COLORS = {
  ema: "#2962FF",
  vwap: "#ffffff",
  rolling_vwap: "#a78bfa",
} as const;

/** Line width for session-anchored VWAP segments on the candlestick chart. */
export const VWAP_LINE_WIDTH = 2;

export function symbolShort(symbol: string): string {
  return symbol.replace("-PERP.BINANCE", "");
}

export function presetId(preset: IndicatorPreset): string {
  if (preset.type === "liquidations") return "liquidations";
  if (preset.type === "vwap") return "vwap";
  if (preset.type === "rolling_vwap") return "rolling_vwap";
  return "ema";
}

export function isPresetActive(indicators: ChartIndicator[], preset: IndicatorPreset): boolean {
  return indicators.some((i) => i.id === presetId(preset));
}

export function indicatorLabel(ind: ChartIndicator): string {
  if (ind.type === "liquidations") {
    const t = ind.threshold ?? DEFAULT_LIQ_THRESHOLD;
    return `Liquidations ($${t >= 1000 ? `${Math.round(t / 1000)}k` : t})`;
  }
  if (ind.type === "vwap") return `VWAP ${ind.period}`;
  if (ind.type === "rolling_vwap") return `Rolling VWAP ${ind.period}`;
  if (ind.type === "session_vwap") return `VWAP ${ind.period}`;
  return `EMA ${ind.period}`;
}

export function getEmaPeriod(indicators: ChartIndicator[]): number {
  const ema = indicators.find((i) => i.type === "ema");
  return ema?.type === "ema" ? ema.period : DEFAULT_EMA_PERIOD;
}

export function getVwapPeriod(indicators: ChartIndicator[]): number {
  const vwap = indicators.find((i) => i.type === "vwap");
  return vwap?.type === "vwap" ? vwap.period : DEFAULT_VWAP_PERIOD;
}

export function getRollingVwapPeriod(indicators: ChartIndicator[]): number {
  const rv = indicators.find((i) => i.type === "rolling_vwap");
  return rv?.type === "rolling_vwap" ? rv.period : DEFAULT_ROLLING_VWAP_PERIOD;
}

export function getLiqThreshold(indicators: ChartIndicator[]): number {
  const liq = indicators.find((i) => i.type === "liquidations");
  return liq?.type === "liquidations" ? (liq.threshold ?? DEFAULT_LIQ_THRESHOLD) : DEFAULT_LIQ_THRESHOLD;
}

export function maColor(index: number): string {
  return MA_COLORS[index % MA_COLORS.length];
}

export function indicatorLineColor(
  type: "ema" | "vwap" | "rolling_vwap" | "session_vwap"
): string {
  if (type === "session_vwap") return INDICATOR_LINE_COLORS.vwap;
  if (type === "rolling_vwap") return INDICATOR_LINE_COLORS.rolling_vwap;
  return INDICATOR_LINE_COLORS[type as "ema" | "vwap"];
}
