import { describe, expect, it } from "vitest";
import { computeSessionHorizontalSegments } from "./sessionHorizontalLines";

describe("computeSessionHorizontalSegments", () => {
  it("returns empty for no bars", () => {
    expect(computeSessionHorizontalSegments([], 15)).toEqual([]);
  });

  it("emits one 15m segment per session bucket overlapping range", () => {
    const sessionSec = 15 * 60;
    const start = Math.floor(1_700_000_000 / sessionSec) * sessionSec;
    const bars = [
      { time: start, open: 100 },
      { time: start + sessionSec, open: 110 },
      { time: start + sessionSec * 2, open: 120 },
    ];
    const segments = computeSessionHorizontalSegments(bars, 15);
    expect(segments).toHaveLength(3);
    expect(segments[0]).toEqual({
      start,
      end: start + sessionSec,
      price: 100,
    });
    expect(segments[1].price).toBe(110);
    expect(segments[2].price).toBe(120);
  });

  it("uses open of first bar in session when bucket open is missing", () => {
    const sessionSec = 15 * 60;
    const sessionStart = Math.floor(1_700_000_000 / sessionSec) * sessionSec;
    const bars = [{ time: sessionStart + 60, open: 99 }];
    const segments = computeSessionHorizontalSegments(bars, 15);
    expect(segments).toHaveLength(1);
    expect(segments[0].start).toBe(sessionStart);
    expect(segments[0].price).toBe(99);
  });
});
