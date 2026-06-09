import type { UTCTimestamp } from "lightweight-charts";
import type { PaperEventMsg } from "../types";
import { barOpenTime } from "./barTime";

export const TRADE_LONG_COLOR = "#22c55e";
export const TRADE_SHORT_COLOR = "#ef4444";

export type TradeSignalMarker = {
  id: string;
  time: UTCTimestamp;
  direction: "LONG" | "SHORT";
};

export function parseUnderlyingSymbol(msg: PaperEventMsg): string | null {
  if (msg.underlying) return msg.underlying;
  const tip = msg.entry_signal_tooltip ?? "";
  const m = /sym=([^,;]+)/.exec(tip);
  return m ? m[1].trim() : null;
}

export function paperFillMatchesSymbol(
  msg: PaperEventMsg,
  chartSymbol: string
): boolean {
  return parseUnderlyingSymbol(msg) === chartSymbol;
}

export function parseUnderlyingDirection(
  msg: PaperEventMsg
): "LONG" | "SHORT" | null {
  const d = msg.underlying_direction;
  if (d === "LONG" || d === "SHORT") return d;
  const tip = msg.entry_signal_tooltip ?? "";
  const m = /dir=(LONG|SHORT)/.exec(tip);
  return m ? (m[1] as "LONG" | "SHORT") : null;
}

export function tradeMarkerKey(msg: PaperEventMsg): string {
  return msg.client_order_id ?? `ts-${msg.ts}`;
}

export function tradeMarkerForFill(
  msg: PaperEventMsg,
  interval: string
): TradeSignalMarker | null {
  if (msg.kind !== "fill") return null;
  const dir = parseUnderlyingDirection(msg);
  if (!dir) return null;
  return {
    id: tradeMarkerKey(msg),
    time: barOpenTime(Math.floor(msg.ts / 1e9), interval) as UTCTimestamp,
    direction: dir,
  };
}
