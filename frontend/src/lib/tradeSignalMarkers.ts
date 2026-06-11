import type { UTCTimestamp } from "lightweight-charts";
import type { PaperEventMsg } from "../types";
import { binancePerpToPolySeries } from "./binancePolySeries";
import { barOpenTime } from "./barTime";

export const TRADE_LONG_COLOR = "#22c55e";
export const TRADE_SHORT_COLOR = "#ef4444";
export const TRADE_EXIT_COLOR = "#f59e0b";

export type TradeMarkerAction = "entry" | "exit";

export type TradeSignalMarker = {
  id: string;
  time: UTCTimestamp;
  direction: "LONG" | "SHORT";
  action: TradeMarkerAction;
  stackIndex: number;
};

function parseTaggedField(msg: PaperEventMsg, keys: string[]): string | null {
  const tip = msg.entry_signal_tooltip ?? "";
  for (const key of keys) {
    const m = new RegExp(`${key}=([^,;]+)`).exec(tip);
    if (m) return m[1].trim();
  }
  return null;
}

export function parseUnderlyingSymbol(msg: PaperEventMsg): string | null {
  if (msg.underlying) return msg.underlying;
  return parseTaggedField(msg, ["symbol", "sym"]);
}

export function paperEventMatchesChart(
  msg: PaperEventMsg,
  chartSymbol: string
): boolean {
  const underlying = parseUnderlyingSymbol(msg);
  if (underlying === chartSymbol) return true;
  if (msg.kind !== "position_close") return false;
  const series = msg.market_series;
  const polySeries = binancePerpToPolySeries(chartSymbol);
  return !!(series && polySeries && series === polySeries);
}

/** @deprecated use paperEventMatchesChart */
export function paperFillMatchesSymbol(
  msg: PaperEventMsg,
  chartSymbol: string
): boolean {
  return paperEventMatchesChart(msg, chartSymbol);
}

export function parseUnderlyingDirection(
  msg: PaperEventMsg
): "LONG" | "SHORT" | null {
  const d = msg.underlying_direction;
  if (d === "LONG" || d === "SHORT") return d;
  const parsed = parseTaggedField(msg, ["direction", "dir"]);
  return parsed === "LONG" || parsed === "SHORT" ? parsed : null;
}

export function parseCloseDirection(
  msg: PaperEventMsg
): "LONG" | "SHORT" | null {
  if (msg.market_outcome === "YES") return "LONG";
  if (msg.market_outcome === "NO") return "SHORT";
  return null;
}

export function tradeMarkerKey(msg: PaperEventMsg): string {
  if (msg.kind === "position_close") {
    return `close-${msg.instrument_id}-${msg.ts}`;
  }
  return msg.client_order_id ?? `ts-${msg.ts}`;
}

export function tradeMarkerForPaperEvent(
  msg: PaperEventMsg,
  interval: string
): TradeSignalMarker | null {
  const time = barOpenTime(Math.floor(msg.ts / 1e9), interval) as UTCTimestamp;

  if (msg.kind === "fill") {
    const dir = parseUnderlyingDirection(msg);
    if (!dir) return null;
    return {
      id: tradeMarkerKey(msg),
      time,
      direction: dir,
      action: "entry",
      stackIndex: 0,
    };
  }

  if (msg.kind === "position_close") {
    const dir = parseCloseDirection(msg);
    if (!dir) return null;
    return {
      id: tradeMarkerKey(msg),
      time,
      direction: dir,
      action: "exit",
      stackIndex: 0,
    };
  }

  return null;
}

/** @deprecated use tradeMarkerForPaperEvent */
export function tradeMarkerForFill(
  msg: PaperEventMsg,
  interval: string
): TradeSignalMarker | null {
  return tradeMarkerForPaperEvent(msg, interval);
}
