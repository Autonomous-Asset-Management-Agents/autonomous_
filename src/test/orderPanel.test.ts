import { describe, it, expect } from "vitest";
import { showOrderBookOnOverview } from "../console/live/orderPanel";

/** #2190: the Overview open-orders book is shown ONLY in live trading (a
 * Senior/Plus feature). In paper it's hidden — market orders fill instantly so
 * the book is always empty. Before the first /health poll it stays hidden. */
describe("showOrderBookOnOverview", () => {
  it("live (paper_trading === false) → show the order book", () => {
    expect(showOrderBookOnOverview(false)).toBe(true);
  });
  it("paper (paper_trading === true) → hidden", () => {
    expect(showOrderBookOnOverview(true)).toBe(false);
  });
  it("unknown (null, before first poll) → hidden (paper is the default)", () => {
    expect(showOrderBookOnOverview(null)).toBe(false);
  });
  it("live + full-autonomous (Mode C) → hidden (nothing waits for a human)", () => {
    expect(showOrderBookOnOverview(false, true)).toBe(false);
  });
  it("live + manual/semi-autonomous → shown (orders queue for approval)", () => {
    expect(showOrderBookOnOverview(false, false)).toBe(true);
  });
  it("paper stays hidden regardless of autonomy", () => {
    expect(showOrderBookOnOverview(true, false)).toBe(false);
    expect(showOrderBookOnOverview(true, true)).toBe(false);
  });
});
