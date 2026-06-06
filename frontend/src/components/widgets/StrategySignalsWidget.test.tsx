import { describe, expect, it } from "vitest";
import { STRATEGY_BINANCE_SYMBOLS } from "./StrategySignalsWidget";

describe("StrategySignalsWidget", () => {
  it("exports five strategy binance symbols", () => {
    expect(STRATEGY_BINANCE_SYMBOLS).toHaveLength(5);
    expect(STRATEGY_BINANCE_SYMBOLS.map((s) => s.split("USDT")[0])).toEqual([
      "BTC",
      "ETH",
      "SOL",
      "XRP",
      "DOGE",
    ]);
  });
});
