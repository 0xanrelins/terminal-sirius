import { describe, expect, it } from "vitest";
import {
  COMPLETED_COLOR,
  assignActiveColorIndices,
  chartTimeToMinuteLabel,
  elapsedToChartTime,
  pointsToLineData,
  sessionLineColor,
  sessionsFetchUrl,
  SYNTHETIC_BASE_EPOCH,
  WINDOW_SEC,
  type PostEventSession,
} from "./liqPostEventChart";

const sampleSession = (
  id: string,
  status: "active" | "completed" = "active"
): PostEventSession => ({
  session_id: id,
  symbol: "BTC",
  side: "LONG",
  notional: 100_000,
  anchor_price: 100,
  event_time: 1_700_000_000,
  status,
  points: [{ elapsed_sec: 0, pct: 0 }],
});

describe("liqPostEventChart", () => {
  it("maps elapsed to synthetic chart time", () => {
    expect(elapsedToChartTime(0)).toBe(SYNTHETIC_BASE_EPOCH);
    expect(elapsedToChartTime(WINDOW_SEC)).toBe(SYNTHETIC_BASE_EPOCH + WINDOW_SEC);
  });

  it("formats minute labels", () => {
    expect(chartTimeToMinuteLabel(SYNTHETIC_BASE_EPOCH)).toBe("0m");
    expect(chartTimeToMinuteLabel(SYNTHETIC_BASE_EPOCH + 900)).toBe("15m");
    expect(chartTimeToMinuteLabel(SYNTHETIC_BASE_EPOCH + WINDOW_SEC)).toBe("30m");
  });

  it("builds line data from points", () => {
    const data = pointsToLineData([
      { elapsed_sec: 0, pct: 0 },
      { elapsed_sec: 60, pct: 1.5 },
    ]);
    expect(data).toHaveLength(2);
    expect(data[1].value).toBe(1.5);
  });

  it("uses gray for completed sessions", () => {
    expect(sessionLineColor(sampleSession("a", "completed"), 0)).toBe(COMPLETED_COLOR);
    expect(sessionLineColor(sampleSession("a", "active"), 0)).not.toBe(COMPLETED_COLOR);
  });

  it("builds fetch url with filters and no limit", () => {
    const url = sessionsFetchUrl({
      coins: ["BTC", "ETH"],
      minNotional: 50_000,
      sides: ["LONG"],
    });
    expect(url).toContain("symbols=BTC%2CETH");
    expect(url).toContain("interval=30s");
    expect(url).toContain("min_notional=50000");
    expect(url).toContain("sides=LONG");
    expect(url).not.toContain("limit=");
  });

  it("keeps stable active color indices", () => {
    const s1 = sampleSession("1");
    const s2 = sampleSession("2");
    const first = assignActiveColorIndices([s1, s2], new Map());
    const second = assignActiveColorIndices([s2, s1], first);
    expect(second.get("1")).toBe(first.get("1"));
    expect(second.get("2")).toBe(first.get("2"));
  });
});
