// #3197 (04.09., live account): the Overview showed "Max drawdown −92.81 % since Aug 1, 2026"
// next to "autonomous_ +4.07 %" — from the SAME curve. Measured against the running engine
// (/benchmark-equity, 25 points, baseline 2026-08-01), the real worst peak-to-trough slide was
// −1.85 %: the book went 11,896.74 -> 11,676.15, i.e. −220.59 $ on an 11.9k account.
//
// Cause: computeMaxDrawdownCashflowAdjusted neutralised the deposits IN MONEY — equity minus the
// running cumulative flow. That leaves the account's own pre-funding capital as the base (~237 $)
// while the actual book is 11.9k, so a 220 $ move reads as −93 %. It is a money-weighted
// construct displayed beside time-weighted ones. Drawdown now runs on the SAME chain-linked index
// the return card uses, which is also the standard definition.
import { describe, it, expect } from "vitest";
import { computeMaxDrawdown, computeMaxDrawdownCashflowAdjusted } from "../drawdown";

// The real served curve, verbatim from the live engine on 04.09.2026.
const LIVE: [string, number, number][] = [
  ["2026-08-01", 222.95, 0], ["2026-08-04", 224.04, 0], ["2026-08-05", 224.58, 0],
  ["2026-08-06", 226.23, 0], ["2026-08-07", 224.84, 0], ["2026-08-08", 338.91, 113.78],
  ["2026-08-11", 342.86, 0], ["2026-08-12", 343.58, 0], ["2026-08-13", 343.57, 0],
  ["2026-08-14", 346.46, 0], ["2026-08-15", 347.53, 0], ["2026-08-18", 346.32, 0],
  ["2026-08-19", 345.1, 0], ["2026-08-20", 345.64, 0], ["2026-08-21", 347.97, 0],
  ["2026-08-22", 350.48, 0], ["2026-08-25", 350.9, 0], ["2026-08-26", 6143.64, 5792.74],
  ["2026-08-27", 6144.19, 0], ["2026-08-28", 11896.74, 5752.55], ["2026-08-29", 11838.73, 0],
  ["2026-09-01", 11709.24, 0], ["2026-09-02", 11676.15, 0], ["2026-09-03", 11775.7, 0],
  ["2026-09-04", 11840.92, 0],
];
const liveCurve = LIVE.map(([, eur, cashflow]) => ({ eur, cashflow }));

describe("max drawdown (#3197) — the live vector", () => {
  it("reports the real slide, not the pre-funding base artefact", () => {
    const dd = computeMaxDrawdownCashflowAdjusted(liveCurve);
    // truth: 11,896.74 -> 11,676.15 = −1.85 % of the book
    expect(dd).toBeCloseTo(-1.85, 1);
    expect(dd).toBeGreaterThan(-5); // the −92.81 % card must be impossible
  });

  it("stays consistent with the return card computed from the same curve", () => {
    // The window ended ABOVE its start (+4.07 % TWR), so the drawdown cannot exceed that
    // slide in magnitude by an order of magnitude — the two are read side by side.
    const dd = computeMaxDrawdownCashflowAdjusted(liveCurve);
    expect(Math.abs(dd)).toBeLessThan(10);
  });
});

describe("max drawdown (#3197) — semantics", () => {
  it("a deposit neither creates nor masks a drawdown", () => {
    const noFlow = computeMaxDrawdownCashflowAdjusted([
      { eur: 100 }, { eur: 90 }, { eur: 95 },
    ]);
    const withDeposit = computeMaxDrawdownCashflowAdjusted([
      { eur: 100 }, { eur: 90 }, { eur: 10_090, cashflow: 10_000 },
    ]);
    expect(noFlow).toBeCloseTo(-10, 6);
    expect(withDeposit).toBeCloseTo(-10, 6); // the deposit is capital, not a recovery
  });

  it("a withdrawal is not a drawdown", () => {
    const dd = computeMaxDrawdownCashflowAdjusted([
      { eur: 10_000 }, { eur: 10_000 }, { eur: 4_000, cashflow: -6_000 },
    ]);
    expect(dd).toBeCloseTo(0, 6);
  });

  it("a real slide after a deposit is measured against the FUNDED book", () => {
    // 10k own money, then 90k deposited, then the 100k book loses 10k.
    const dd = computeMaxDrawdownCashflowAdjusted([
      { eur: 10_000 }, { eur: 100_000, cashflow: 90_000 }, { eur: 90_000 },
    ]);
    expect(dd).toBeCloseTo(-10, 6); // −10 % of the book, NOT −100 % of the old base
  });

  it("without cash-flows it matches the plain drawdown exactly", () => {
    const plain = [{ eur: 100 }, { eur: 120 }, { eur: 84 }, { eur: 110 }];
    expect(computeMaxDrawdownCashflowAdjusted(plain)).toBeCloseTo(
      computeMaxDrawdown(plain), 6,
    );
  });

  it("degenerate input yields 0, never a fabricated number", () => {
    expect(computeMaxDrawdownCashflowAdjusted([])).toBe(0);
    expect(computeMaxDrawdownCashflowAdjusted([{ eur: 100 }])).toBe(0);
  });
});
