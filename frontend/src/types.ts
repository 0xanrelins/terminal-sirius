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
  question: string;
  yes_price: number;
  bid?: number;
  ask?: number;
  ts: number;
};

export type FeedMsg = TradeMsg | QuoteMsg | BarMsg | PolymarketMsg;

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

export type WidgetType = "price_ticker" | "candlestick_chart" | "polymarket_ticker";

export type PriceTickerConfig = {
  id: string;
  type: "price_ticker";
  symbol: string;
};

export type CandlestickChartConfig = {
  id: string;
  type: "candlestick_chart";
  symbol: string;
  interval: string;
};

export type PolymarketTickerConfig = {
  id: string;
  type: "polymarket_ticker";
  symbol: string;
  slug: string;
  question: string;
};

export type WidgetConfig = PriceTickerConfig | CandlestickChartConfig | PolymarketTickerConfig;

export type CanvasState = {
  widgets: WidgetConfig[];
  layout: import("react-grid-layout").Layout[];
};

// Multi-dashboard persistence shape
export type DashboardsStorage = {
  dashboards: Record<string, CanvasState>;
  active: string;
};
