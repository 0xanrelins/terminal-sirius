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

// ── Paper-trade monitoring (account-level; no `symbol`) ──────────────────────

/** Polymarket market context (from ActivePolymarketMarket + Cache instrument). */
export type PaperMarketFields = {
  market_label?: string;
  market_slug?: string;
  market_series?: string;
  market_question?: string;
  market_outcome?: string;
  /** e.g. ``June 4, 11:45PM-12:00AM ET`` (from Gamma question or slug window). */
  market_window?: string;
  underlying?: string;
};

export type PaperSettlementOutcome = "won" | "lost" | "push";

export type PaperPosition = {
  instrument_id: string;
  side: string;
  quantity: number | null;
  avg_px_open: number | null;
  avg_px_close?: number | null;
  unrealized_pnl: number | null;
  realized_pnl: number | null;
  /** Set when a 15m market expires (binary 0/1 settlement). */
  settlement_outcome?: PaperSettlementOutcome | null;
  opened_ts: number;
  closed_ts?: number;
  duration_s: number;
} & PaperMarketFields;

export type PaperOrder = {
  client_order_id: string;
  instrument_id: string;
  side: string;
  order_type: string;
  quantity: number | null;
  filled_qty: number | null;
  status: string;
  ts: number;
  entry_signal: string;
  entry_signal_tooltip: string;
} & PaperMarketFields;

export type PaperSnapshotMsg = {
  type: "paper_snapshot";
  ts: number;
  run: {
    strategy_on: boolean;
    paper: boolean;
    trader_id: string;
    venue: string;
    started_ts: number;
    uptime_s: number;
  };
  account: {
    currency: string | null;
    balance: number | null;
    balances: Record<string, number>;
    locked: Record<string, number>;
    equity: number | null;
    equity_all: Record<string, number>;
  } | null;
  pnl: {
    currency?: string | null;
    realized?: number | null;
    unrealized?: number | null;
    total?: number | null;
  };
  exposure: { net?: number | null; net_all?: Record<string, number> };
  positions: PaperPosition[];
  closed_positions?: PaperPosition[];
  orders: PaperOrder[];
  stats: Record<string, number | string>;
  counts: {
    open_positions: number;
    open_orders: number;
    closed_trades: number;
    fills?: number;
  };
};

export type PaperEventKind =
  | "fill"
  | "position_open"
  | "position_close"
  | "position_change"
  | "order_rejected"
  | "order_denied";

export type PaperEventMsg = {
  type: "paper_event";
  kind: PaperEventKind;
  ts: number;
  instrument_id: string;
  side?: string;
  quantity?: number | null;
  price?: number | null;
  commission?: number | null;
  realized_pnl?: number | null;
  settlement_outcome?: PaperSettlementOutcome | null;
  duration_s?: number | null;
  opened_ts?: number;
  closed_ts?: number;
  client_order_id?: string;
  reason?: string;
  entry_signal?: string;
  entry_signal_tooltip?: string;
} & PaperMarketFields;

/** REST `/paper/equity` point (mirrors backend db row). */
export type PaperEquityPoint = {
  ts: number;
  currency: string | null;
  equity: number | null;
  balance: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  total_pnl: number | null;
  net_exposure: number | null;
  open_positions: number;
  open_orders: number;
};

export type StrategySignalSymbolState = {
  vwap: number | null;
  slope: number | null;
  low_zone: number | null;
  high_zone: number | null;
  close: number | null;
  vwap_ready: boolean;
  long_volume: number;
  short_volume: number;
  liq_threshold: number | null;
  liq_long_hit: boolean;
  liq_short_hit: boolean;
  liq_long_trigger: boolean;
  liq_short_trigger: boolean;
  in_range: boolean;
  long_zone: boolean;
  short_zone: boolean;
  decision: "LONG" | "SHORT" | "HOLD";
};

export type StrategySignalSnapshotMsg = {
  type: "strategy_signal_snapshot";
  ts: number;
  symbols: Record<string, StrategySignalSymbolState>;
};

export type FeedMsg =
  | TradeMsg
  | QuoteMsg
  | BarMsg
  | IndicatorMsg
  | PolymarketMsg
  | LiquidationMsg
  | PaperSnapshotMsg
  | PaperEventMsg
  | StrategySignalSnapshotMsg;

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
  | "bar_countdown"
  | "new_york_time"
  | "paper_trade_dashboard"
  | "strategy_signals";

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

export type NewYorkTimeConfig = {
  id: string;
  type: "new_york_time";
};

export type PaperTradeDashboardConfig = {
  id: string;
  type: "paper_trade_dashboard";
  /** Equity-curve metric to plot. Default "equity". */
  curveMetric?: "equity" | "total_pnl";
};

export type StrategySignalsConfig = {
  id: string;
  type: "strategy_signals";
  /** Binance perp ids to show; omit = all strategy symbols. */
  symbols?: string[];
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
  | BarCountdownConfig
  | NewYorkTimeConfig
  | PaperTradeDashboardConfig
  | StrategySignalsConfig;

export type CanvasState = {
  widgets: WidgetConfig[];
  layout: import("react-grid-layout").Layout[];
};

// Multi-dashboard persistence shape
export type DashboardsStorage = {
  dashboards: Record<string, CanvasState>;
  active: string;
};
