// #2717 hero fix: the Overview hero range-change must be deposit-neutral (TWR),
// like the "since inception" KPI and the chart — a deposit in the window must NOT
// show up as a gain. Regression proof for the "+54 % past week" screenshot.
import { describe, expect, it } from "vitest";

import { rangeReturn, rangeReturnCashflowAdjusted } from "../equityRange";

const P = (eur: number, cashflow = 0, dayOffset = 0) => ({
  t: new Date(2026, 0, 1 + dayOffset),
  eur,
  cashflow,
});

describe("rangeReturnCashflowAdjusted", () => {
  it("does NOT count a deposit into a flat market as a gain", () => {
    // equity 100 -> 200 purely from a +100 deposit; the market itself was flat
    const curve = [P(100, 0, 0), P(200, 100, 1)];
    // the raw range-return (today's bug) reports +100 %
    expect(rangeReturn(curve, "ALL")!.pct).toBeCloseTo(100, 6);
    // the deposit-neutral hero must report ~0 % and ~0 market P&L
    const adj = rangeReturnCashflowAdjusted(curve, "ALL")!;
    expect(adj.pct).toBeCloseTo(0, 6);
    expect(adj.abs).toBeCloseTo(0, 6);
  });

  it("reflects only the market move when a deposit and a gain coincide", () => {
    // day1: +100 deposit, market flat (100->200). day2: +10 % market (200->220), no flow.
    const curve = [P(100, 0, 0), P(200, 100, 1), P(220, 0, 2)];
    const adj = rangeReturnCashflowAdjusted(curve, "ALL")!;
    expect(adj.pct).toBeCloseTo(10, 6); // only the +10 % market move, not +120 %
    expect(adj.abs).toBeCloseTo(20, 6); // the +20 market P&L, deposit stripped
  });

  it("matches the raw range-return when there are no cash-flows", () => {
    const curve = [P(100, 0, 0), P(110, 0, 1)];
    expect(rangeReturnCashflowAdjusted(curve, "ALL")!.pct).toBeCloseTo(
      rangeReturn(curve, "ALL")!.pct,
      6,
    );
  });

  it("returns null for fewer than two points", () => {
    expect(rangeReturnCashflowAdjusted([P(100)], "ALL")).toBeNull();
  });
});
