import { describe, expect, it } from "vitest";
import {
  calculateSessionVWAP,
  calculateSessionVWAPSegments,
  type OhlcvBar,
} from "./chartIndicators";

function barAt(
  timeSec: number,
  close: number,
  volume: number,
  high = close,
  low = close
): OhlcvBar {
  return {
    time: timeSec as OhlcvBar["time"],
    open: close,
    high,
    low,
    close,
    volume,
  };
}

describe("calculateSessionVWAPSegments", () => {
  it("accumulates within a UTC session bucket (1m × period 3 = 3 minutes)", () => {
    const bars = [
      barAt(0, 10, 10),
      barAt(60, 20, 10),
      barAt(120, 30, 10),
    ];
    const segments = calculateSessionVWAPSegments(bars, 3, "1m");

    expect(segments).toHaveLength(1);
    expect(segments[0][0].value).toBe(10);
    expect(segments[0][1].value).toBe(15);
    expect(segments[0][2].value).toBe(20);
  });

  it("splits into separate segments at session boundaries", () => {
    const bars = [
      barAt(0, 10, 10),
      barAt(60, 20, 10),
      barAt(120, 30, 10),
      barAt(180, 40, 10),
      barAt(240, 50, 10),
    ];
    const segments = calculateSessionVWAPSegments(bars, 3, "1m");

    expect(segments).toHaveLength(2);
    expect(segments[0]).toHaveLength(3);
    expect(segments[1]).toHaveLength(2);
    expect(segments[1][0].value).toBe(40);
    expect(segments[1][1].value).toBe(45);
  });

  it("aligns 15m sessions on 1m chart (period 15)", () => {
    const sessionStart = 1_700_000_100;
    const bars = [
      barAt(sessionStart, 10, 10),
      barAt(sessionStart + 14 * 60, 20, 10),
      barAt(sessionStart + 15 * 60, 30, 10),
    ];
    const segments = calculateSessionVWAPSegments(bars, 15, "1m");

    expect(segments).toHaveLength(2);
    expect(segments[0]).toHaveLength(2);
    expect(segments[1]).toHaveLength(1);
    expect(segments[1][0].value).toBe(30);
  });

  it("flat() matches segment points in order", () => {
    const bars = [barAt(0, 10, 10), barAt(180, 20, 10)];
    const flat = calculateSessionVWAP(bars, 3, "1m");
    const segments = calculateSessionVWAPSegments(bars, 3, "1m").flat();
    expect(flat.map((p) => p.value)).toEqual(segments.map((p) => p.value));
  });
});
