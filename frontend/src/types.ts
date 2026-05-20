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

export type LiquidationMsg = {
  type: "liquidation";
  symbol: string;
  side: string;
  notional: number;
  time: number;
  trade_id?: number;
};

export type SimulationSide = "long" | "short";

export type SimulationSignalMsg = {
  type: "simulation_signal";
  side: SimulationSide;
  asset: string;
  cycle_id: number;
  binance_symbol: string;
  poly_series: string;
  signal_time: number;
  signal_long_notional?: number;
  signal_short_notional?: number;
  threshold: number;
  liq_bar_open?: number;
  target_candle_open: number;
};

export type SimulationBetOpenMsg = {
  type: "simulation_bet_open";
  bet_id: number;
  cycle_id: number;
  side: SimulationSide;
  asset: string;
  leg: number;
  binance_symbol: string;
  poly_series: string;
  poly_slug: string;
  candle_open: number;
  entry_price: number;
  shares: number;
  cost_usd: number;
  opened_at: number;
  signal_time?: number;
  liq_bar_open?: number;
};

export type SimulationBetSettleMsg = {
  type: "simulation_bet_settle";
  bet_id: number;
  cycle_id: number;
  side: SimulationSide;
  asset: string;
  leg: number;
  candle_open: number;
  outcome: "win" | "loss";
  pnl_usd: number;
  won: boolean;
  candle_green: boolean;
  settled_at: number;
};

export type SimulationCycleClosedMsg = {
  type: "simulation_cycle_closed";
  cycle_id: number;
  asset: string;
  side: SimulationSide;
};

export type SimulationMsg =
  | SimulationSignalMsg
  | SimulationBetOpenMsg
  | SimulationBetSettleMsg
  | SimulationCycleClosedMsg;

export type LiveSignalMsg = {
  type: "live_signal";
  side: SimulationSide;
  asset: string;
  cycle_id?: number;
  binance_symbol: string;
  poly_series: string;
  signal_time: number;
  signal_long_notional?: number;
  signal_short_notional?: number;
  threshold: number;
  liq_bar_open?: number;
  target_candle_open: number;
  dry_run?: boolean;
};

export type LiveBetOpenMsg = {
  type: "live_bet_open";
  bet_id: number;
  cycle_id: number;
  side: SimulationSide;
  asset: string;
  leg: number;
  binance_symbol: string;
  poly_series: string;
  poly_slug: string;
  candle_open: number;
  entry_price: number;
  shares: number;
  cost_usd: number;
  opened_at: number;
  signal_time?: number;
  liq_bar_open?: number;
  order_id?: string | null;
  clob_status?: string | null;
};

export type LiveBetSettleMsg = {
  type: "live_bet_settle";
  bet_id: number;
  cycle_id: number;
  side: SimulationSide;
  asset: string;
  leg: number;
  candle_open: number;
  outcome: "win" | "loss";
  pnl_usd: number;
  won: boolean;
  candle_green: boolean;
  settled_at: number;
  order_id?: string | null;
};

export type LiveCycleClosedMsg = {
  type: "live_cycle_closed";
  cycle_id: number;
  asset: string;
  side: SimulationSide;
};

export type LiveOrderErrorMsg = {
  type: "live_order_error";
  asset: string;
  side: SimulationSide;
  leg: number;
  poly_slug: string;
  error: string;
};

export type LiveMsg =
  | LiveSignalMsg
  | LiveBetOpenMsg
  | LiveBetSettleMsg
  | LiveCycleClosedMsg
  | LiveOrderErrorMsg;

export type SimulationBetRow = {
  id: number;
  cycle_id: number;
  side: SimulationSide;
  leg: number;
  candle_open: number;
  poly_slug: string;
  poly_series: string;
  entry_price: number;
  shares: number;
  cost_usd: number;
  outcome: string | null;
  pnl_usd: number | null;
  opened_at: number;
  settled_at: number | null;
  signal_time: number;
  liq_bar_open?: number | null;
  asset: string;
};

export type SimulationSideStats = {
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl_usd: number;
  open_bets: number;
};

export type SimulationStatus = {
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl_usd: number;
  open_bets: number;
  active_cycles: number;
  by_side?: Record<string, SimulationSideStats>;
  enabled?: boolean;
  thresholds?: Record<string, number>;
  min_usd?: number;
  min_shares?: number;
};

export type LiveBetRow = {
  id: number;
  cycle_id: number;
  side: SimulationSide;
  leg: number;
  candle_open: number;
  poly_slug: string;
  poly_series: string;
  entry_price: number;
  shares: number;
  cost_usd: number;
  outcome: string | null;
  pnl_usd: number | null;
  opened_at: number;
  settled_at: number | null;
  signal_time: number;
  liq_bar_open?: number | null;
  asset: string;
  order_id?: string | null;
  clob_status?: string | null;
  fill_price?: number | null;
};

export type LiveStatus = {
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl_usd: number;
  open_bets: number;
  active_cycles: number;
  by_side?: Record<string, SimulationSideStats>;
  enabled?: boolean;
  orders_enabled?: boolean;
  credentials_configured?: boolean;
  thresholds?: Record<string, number>;
  assets?: string[];
  min_usd?: number;
  min_shares?: number;
};

export type FeedMsg =
  | TradeMsg
  | QuoteMsg
  | BarMsg
  | PolymarketMsg
  | LiquidationMsg
  | SimulationMsg
  | LiveMsg;

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
  | "polymarket_ticker"
  | "liquidation_signals"
  | "simulation_panel"
  | "live_trade_panel"
  | "market_times";

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
  | { id: string; type: "liquidations"; threshold?: number };

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

export type PolymarketTickerConfig = {
  id: string;
  type: "polymarket_ticker";
  symbol: string;
  slug: string;
  question: string;
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

export type SimulationPanelConfig = {
  id: string;
  type: "simulation_panel";
};

export type LiveTradePanelConfig = {
  id: string;
  type: "live_trade_panel";
};

export type MarketTimesConfig = {
  id: string;
  type: "market_times";
};

export type WidgetConfig =
  | PriceTickerConfig
  | CandlestickChartConfig
  | ComparisonChartConfig
  | PolymarketTickerConfig
  | LiquidationSignalsConfig
  | SimulationPanelConfig
  | LiveTradePanelConfig
  | MarketTimesConfig;

export type CanvasState = {
  widgets: WidgetConfig[];
  layout: import("react-grid-layout").Layout[];
};

// Multi-dashboard persistence shape
export type DashboardsStorage = {
  dashboards: Record<string, CanvasState>;
  active: string;
};
