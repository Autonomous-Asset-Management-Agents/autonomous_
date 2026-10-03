import { describe, it, expect } from "vitest";
import { nearestIndex } from "../console/live/chartHover";

/** PR-A crosshair: map a horizontal pointer fraction (0..1) across the plot to
 *  the nearest evenly-spaced data index, so the tooltip reads the real point. */
describe("nearestIndex (PR-A crosshair)", () => {
  it("maps a fraction to the nearest evenly-spaced index", () => {
    expect(nearestIndex(0, 5)).toBe(0);
    expect(nearestIndex(1, 5)).toBe(4);
    expect(nearestIndex(0.5, 5)).toBe(2);
    expect(nearestIndex(0.1, 5)).toBe(0); // 0.1*4 = 0.4 → 0
    expect(nearestIndex(0.9, 5)).toBe(4); // 0.9*4 = 3.6 → 4
  });

  it("clamps out-of-range fractions into the series", () => {
    expect(nearestIndex(-0.5, 5)).toBe(0);
    expect(nearestIndex(1.5, 5)).toBe(4);
  });

  it("returns null when there is nothing to hover (n<2) or NaN", () => {
    expect(nearestIndex(0.5, 0)).toBeNull();
    expect(nearestIndex(0.5, 1)).toBeNull();
    expect(nearestIndex(Number.NaN, 5)).toBeNull();
  });
});
