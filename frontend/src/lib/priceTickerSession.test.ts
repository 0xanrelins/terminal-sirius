import { describe, expect, it } from "vitest";

/** Mirrors PriceTicker session-slug reset predicate. */
function shouldResetOnSlugChange(
  prevSlug: string | null,
  nextSlug: string | undefined
): boolean {
  return (
    nextSlug !== undefined &&
    prevSlug !== null &&
    nextSlug !== prevSlug
  );
}

describe("PriceTicker session slug reset", () => {
  it("does not reset on first slug", () => {
    expect(shouldResetOnSlugChange(null, "btc-updown-15m-123")).toBe(false);
  });

  it("resets when slug changes at session boundary", () => {
    expect(
      shouldResetOnSlugChange(
        "btc-updown-15m-123",
        "btc-updown-15m-456"
      )
    ).toBe(true);
  });

  it("does not reset when slug unchanged", () => {
    expect(
      shouldResetOnSlugChange(
        "btc-updown-15m-123",
        "btc-updown-15m-123"
      )
    ).toBe(false);
  });
});
