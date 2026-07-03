import { describe, it, expect } from "vitest";
import { adaptPortfolio } from "../console/live/portfolio";
import type { PortfolioSummaryResponse } from "../lib/api";

/**
 * G3 (#1050): the engine's /portfolio-summary shape (snake_case, per-share
 * price not given) must map to the console's Position shape (camelCase, with
 * derived avg-entry, last price, book weight). This derivation math is the one
 * place a bug hides, so it's TDD-pinned.
 */
const resp = (over: Partial<PortfolioSummaryResponse> = {}): PortfolioSummaryResponse => ({
  status: "success",
  equity: 100_000,
  positions: [
    { symbol: "AAPL", qty: 10, market_value: 2000, unrealized_pnl: 200, unrealized_pnl_pct: 11.1, days_held: 5 },
  ],
  ...over,
});

describe("adaptPortfolio", () => {
  it("derives last price and avg entry from market value / pnl", () => {
    const p = adaptPortfolio(resp()).positions[0];
    expect(p.last).toBeCloseTo(200); // market_value / qty = 2000/10
    expect(p.avgEntry).toBeCloseTo(180); // (market_value - unrealized_pnl)/qty = 1800/10
    expect(p.marketValue).toBe(2000);
    expect(p.unrealizedEUR).toBe(200);
    expect(p.unrealizedPct).toBe(11.1);
    expect(p.heldDays).toBe(5);
  });

  it("derives book weight from equity", () => {
    const p = adaptPortfolio(resp()).positions[0];
    expect(p.weight).toBeCloseTo(2); // 2000 / 100000 * 100
  });

  it("derives cash as equity minus invested", () => {
    const out = adaptPortfolio(resp());
    expect(out.cashEUR).toBe(98_000); // 100000 - 2000
    expect(out.currentEquity).toBe(100_000);
  });

  it("guards against a zero-qty position (no div-by-zero)", () => {
    const p = adaptPortfolio(
      resp({ positions: [{ symbol: "X", qty: 0, market_value: 0, unrealized_pnl: 0, unrealized_pnl_pct: 0 }] }),
    ).positions[0];
    expect(Number.isFinite(p.last)).toBe(true);
    expect(Number.isFinite(p.avgEntry)).toBe(true);
    expect(p.last).toBe(0);
  });

  it("falls back to symbol when no company name is provided", () => {
    expect(adaptPortfolio(resp()).positions[0].name).toBe("AAPL");
  });

  it("handles an empty / error response without throwing", () => {
    expect(adaptPortfolio({ status: "error" }).positions).toEqual([]);
    expect(adaptPortfolio({ status: "error" }).cashEUR).toBeNull();
    expect(adaptPortfolio({ status: "success", positions: [] }).positions).toEqual([]);
  });

  it("weight is 0 when equity is unknown (avoids NaN)", () => {
    const p = adaptPortfolio(resp({ equity: undefined })).positions[0];
    expect(p.weight).toBe(0);
    expect(adaptPortfolio(resp({ equity: undefined })).cashEUR).toBeNull();
  });

  it("preserves a real 0 (flat pnl) instead of masking it", () => {
    // unrealized_pnl_pct: 0 is a real flat position, not missing data.
    const p = adaptPortfolio(
      resp({ positions: [{ symbol: "F", qty: 5, market_value: 500, unrealized_pnl: 0, unrealized_pnl_pct: 0 }] }),
    ).positions[0];
    expect(p.unrealizedEUR).toBe(0);
    expect(p.unrealizedPct).toBe(0);
    expect(p.avgEntry).toBeCloseTo(100); // (500-0)/5, not masked to 0
  });

  it("handles a short position (qty < 0) with correct positive prices", () => {
    // short: negative market_value (liability) / negative qty → positive price.
    const p = adaptPortfolio(
      resp({ positions: [{ symbol: "S", qty: -10, market_value: -2000, unrealized_pnl: -100, unrealized_pnl_pct: -5 }] }),
    ).positions[0];
    expect(p.last).toBeCloseTo(200); // -2000 / -10
    expect(p.avgEntry).toBeCloseTo(190); // (-2000 - -100)/-10 = -1900/-10
    expect(p.qty).toBe(-10);
  });
});
