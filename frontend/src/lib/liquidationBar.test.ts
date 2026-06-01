import { describe, expect, it } from "vitest";
import {
  liqHistColor,
  liqHistValue,
  liquidationBarForChart,
} from "./liquidationBar";
import type { LiquidationBarSnapshot } from "../types";

const bars: LiquidationBarSnapshot[] = [
  { interval: "5s", time: 100, long: 10, short: 0 },
  { interval: "15m", time: 0, long: 10, short: 0 },
];

describe("liquidationBarForChart", () => {
  it("matches chart interval directly", () => {
    expect(liquidationBarForChart(bars, "5s")).toEqual(bars[0]);
  });

  it("falls back to 5s bucket on 1s chart", () => {
    expect(liquidationBarForChart(bars, "1s")).toEqual(bars[0]);
  });

  it("returns undefined when no matching bucket", () => {
    expect(liquidationBarForChart(bars, "1m")).toBeUndefined();
  });
});

describe("liqHistValue and liqHistColor", () => {
  const threshold = 50_000;

  it("shows muted color below threshold but still has value", () => {
    expect(liqHistValue(147, 0, threshold)).toBe(147);
    expect(liqHistColor(147, 0, threshold)).toBe("#4a4a55");
  });

  it("highlights long when long alone exceeds threshold", () => {
    expect(liqHistValue(60_000, 100, threshold)).toBe(60_000);
    expect(liqHistColor(60_000, 100, threshold)).toBe("#ef4444");
  });
});
