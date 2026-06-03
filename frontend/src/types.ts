export type TradeMsg = {
  type: "trade";
  symbol: string;
  price: string;
  size: string;
  side: string;
  ts: number;
};

export type QuoteMsg = {
  type: "quote";
  symbol: string;
  bid: string;
  ask: string;
  bid_size: string;
  ask_size: string;
  ts: number;
};

export type BarMsg = {
  type: "bar";
  symbol: string;
  interval: string;
  /** Bar open time (unix seconds), aligned to interval bucket. */
  time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  ts: number;
};

/** Backend-computed indicator point (live 1s/5s only). */
export type IndicatorMsg = {
  type: "indicator";
  symbol: string;
  interval: string;
  /** Bar open time (unix seconds). */
  time: number;
  indicator: "ema" | "vwap" | "rolling_vwap";
  period: number;
  /** Omitted during EMA warmup. */
  value?: string;
};

export type PolymarketMsg = {
  type: "polymarket";
  symbol: string;
  slug: string;
  series?: string;
  question: string;
  yes_price: number;
  bid?: number;
  ask?: number;
  ts: number;
};

export type LiquidationBarSnapshot = {
  interval: string;
  time: number;
  long: number;
  short: number;
};

export type LiquidationMsg = {
  type: "liquidation";
  symbol: string;
  side: string;
  notional: number;
  time: number;
  trade_id?: number;
  bars?: LiquidationBarSnapshot[];
};

export type FeedMsg =
  | TradeMsg
  | QuoteMsg
  | BarMsg
  | IndicatorMsg
  | PolymarketMsg
  | LiquidationMsg;

export type Kline = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type PolymarketMarket = {
  slug: string;
  question: string;
  yes_price: number | null;
  volume: number;
  active: boolean;
};

export type WidgetType =
  | "price_ticker"
  | "candlestick_chart"
  | "comparison_chart"
  | "liq_post_event_chart"
  | "polymarket_seconds_chart"
  | "polymarket_ticker"
  | "liquidation_signals"
  | "market_times"
  | "bar_countdown";

export type PriceTickerConfig = {
  id: string;
  type: "price_ticker";
  symbol: string;
  /** binance (default) or polymarket rolling 15m */
  source?: "binance" | "polymarket";
  /** Stable series id for polymarket, e.g. btc-updown-15m */
  series?: string;
  label?: string;
};

export type ChartIndicator =
  | { id: string; type: "ema"; period: number }
  | { id: string; type: "vwap"; period: number }
  | { id: string; type: "rolling_vwap"; period: number }
  | { id: string; type: "session_vwap"; period: number }
  | { id: string; type: "liquidations"; threshold?: number }
  | { id: string; type: "polymarket_up" }
  | { id: string; type: "session_breaks"; periodMinutes: number }
  | { id: string; type: "session_hlines"; periodMinutes: number };

export type ChartStyle = "candlestick" | "line";

export type LiquidationBar = {
  time: number;
  long: number;
  short: number;
};

export type CandlestickChartConfig = {
  id: string;
  type: "candlestick_chart";
  symbol: string;
  interval: string;
  chartStyle?: ChartStyle;
  indicators?: ChartIndicator[];
  /** Candles to load/show on first open only. Default 500. */
  initialBars?: number;
};

export type ComparisonChartConfig = {
  id: string;
  type: "comparison_chart";
  interval: string;
  /** Feed symbols shown on the chart (subset of COMPARISON_SYMBOLS). */
  symbols?: string[];
};

export type LiqPostEventSide = "LONG" | "SHORT";
export type LiqPostEventChartInterval = "30s";

export type LiqPostEventChartConfig = {
  id: string;
  type: "liq_post_event_chart";
  /** @deprecated ignored — chart always uses 30s */
  interval?: LiqPostEventChartInterval | "1s" | "5s";
  /** Asset tickers: BTC, ETH, SOL, XRP, DOGE */
  coins?: string[];
  sides?: LiqPostEventSide[];
  minNotional?: number;
};

export type PolymarketTickerConfig = {
  id: string;
  type: "polymarket_ticker";
  symbol: string;
  slug: string;
  question: string;
};

export type PolymarketSecondsChartConfig = {
  id: string;
  type: "polymarket_seconds_chart";
  /** e.g. btc-updown-15m */
  series: string;
  interval?: "1s" | "5s";
  label?: string;
};

export type LiquidationSignalRow = {
  id: string;
  symbol: string;
  side: "LONG" | "SHORT";
  notional: number;
  time: number;
};

export type LiquidationSignalsConfig = {
  id: string;
  type: "liquidation_signals";
  minNotional?: number;
  /** Asset tickers to show (subset of BTC, ETH, SOL, DOGE, XRP). */
  coins?: string[];
  history?: LiquidationSignalRow[];
  /** Bump when storage shape/filter rules change to reset persisted rows. */
  historyVersion?: number;
};

export type MarketTimesConfig = {
  id: string;
  type: "market_times";
};

export type BarCountdownConfig = {
  id: string;
  type: "bar_countdown";
};

export type WidgetConfig =
  | PriceTickerConfig
  | CandlestickChartConfig
  | ComparisonChartConfig
  | LiqPostEventChartConfig
  | PolymarketSecondsChartConfig
  | PolymarketTickerConfig
  | LiquidationSignalsConfig
  | MarketTimesConfig
  | BarCountdownConfig;

export type CanvasState = {
  widgets: WidgetConfig[];
  layout: import("react-grid-layout").Layout[];
};

// Multi-dashboard persistence shape
export type DashboardsStorage = {
  dashboards: Record<string, CanvasState>;
  active: string;
};
