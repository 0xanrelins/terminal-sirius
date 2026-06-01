import { describe, expect, it } from "vitest";
import { binancePerpToPolySeries, polySeriesToFeedSymbol } from "./binancePolySeries";

describe("binancePerpToPolySeries", () => {
  it("maps BTC and ETH perps", () => {
    expect(binancePerpToPolySeries("BTCUSDT-PERP.BINANCE")).toBe("btc-updown-15m");
    expect(binancePerpToPolySeries("ETHUSDT-PERP.BINANCE")).toBe("eth-updown-15m");
  });

  it("returns null for symbols without Polymarket 15m", () => {
    expect(binancePerpToPolySeries("HYPEUSDT-PERP.BINANCE")).toBeNull();
    expect(binancePerpToPolySeries("BNBUSDT-PERP.BINANCE")).toBeNull();
  });
});

describe("polySeriesToFeedSymbol", () => {
  it("appends POLYMARKET suffix", () => {
    expect(polySeriesToFeedSymbol("btc-updown-15m")).toBe("btc-updown-15m.POLYMARKET");
  });
});
