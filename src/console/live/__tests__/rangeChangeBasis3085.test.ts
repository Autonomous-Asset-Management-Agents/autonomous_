// rc5 live finding R6 (01.09.): the Overview hero showed "+$11,776.22  −0.53 % past week"
// — in GREEN — on an account that was three days old. Both halves come from
// rangeReturnCashflowAdjusted, but from DIFFERENT bases:
//   • pct chain-links the sub-periods and SKIPS the ones with a non-positive base — so the
//     pre-funding zero-equity hours Alpaca returns for period=1W contribute nothing (correct);
//   • abs was end − start − netFlow, a plain endpoint difference that still counts exactly
//     those skipped sub-periods — so the account's whole value (11,812.43 − 0 − 36.21 =
//     11,776.22, the screenshot to the cent) was reported as market P&L.
// And the hero's colour followed `abs`, so a loss was painted green.
// Under test: abs and pct always share one basis (same sign, same skips) and the tone
// helper follows the percentage.
import { describe, it, expect } from "vitest";
import { rangeReturnCashflowAdjusted } from "../equityRange";
import { changeTone } from "../equityRange";

const t = (d: string) => new Date(d);

describe("rangeReturnCashflowAdjusted (rc5 R6) — one basis for money and percent", () => {
  it("the 01.09. screenshot: pre-funding zeros do not become market P&L", () => {
    const curve = [
      { t: t("2026-08-25T13:00:00Z"), eur: 0 },
      { t: t("2026-08-26T13:00:00Z"), eur: 0 },
      { t: t("2026-08-27T13:00:00Z"), eur: 11_875.0, cashflow: 36.21 },
      { t: t("2026-09-01T13:00:00Z"), eur: 11_812.43 },
    ];
    const r = rangeReturnCashflowAdjusted(curve, "ALL")!;
    expect(r).not.toBeNull();
    expect(r.abs).toBeLessThan(0); // it was a LOSS, not +$11,776.22
    expect(Math.abs(r.abs)).toBeLessThan(500);
    expect(r.pct).toBeLessThan(0);
  });

  it("money and percent never disagree in sign", () => {
    const shapes = [
      [{ t: t("2026-08-01"), eur: 0 }, { t: t("2026-08-02"), eur: 10_000, cashflow: 10_000 }, { t: t("2026-08-03"), eur: 9_900 }],
      [{ t: t("2026-08-01"), eur: 10_000 }, { t: t("2026-08-02"), eur: 15_300, cashflow: 5_300 }],
      [{ t: t("2026-08-01"), eur: 10_000 }, { t: t("2026-08-02"), eur: 11_000 }],
      [{ t: t("2026-08-01"), eur: 100 }, { t: t("2026-08-02"), eur: 90 }],
    ];
    for (const s of shapes) {
      const r = rangeReturnCashflowAdjusted(s, "ALL")!;
      expect(Math.sign(Math.round(r.abs * 100)), JSON.stringify(s)).toBe(
        Math.sign(Math.round(r.pct * 1e6)),
      );
    }
  });

  it("a pure deposit is neither money nor percent gain", () => {
    const r = rangeReturnCashflowAdjusted(
      [
        { t: t("2026-08-01"), eur: 10_000 },
        { t: t("2026-08-02"), eur: 15_300, cashflow: 5_300 },
      ],
      "ALL",
    )!;
    expect(r.abs).toBeCloseTo(0, 6);
    expect(r.pct).toBeCloseTo(0, 10);
  });

  it("real market P&L across a deposit is still reported in full", () => {
    // 10k → 11k (+1,000), then a 9k deposit lands and the book ends at 22k → market +2,000.
    const r = rangeReturnCashflowAdjusted(
      [
        { t: t("2026-08-01"), eur: 10_000 },
        { t: t("2026-08-02"), eur: 11_000 },
        { t: t("2026-08-03"), eur: 22_000, cashflow: 9_000 },
      ],
      "ALL",
    )!;
    expect(r.abs).toBeCloseTo(3_000, 6);
    expect(r.pct).toBeCloseTo(30, 6);
  });

  it("a settlement-lagged deposit is deferred for money exactly as for percent", () => {
    // flow dated on the PRE-jump point; equity reflects it only on the next one.
    const r = rangeReturnCashflowAdjusted(
      [
        { t: t("2026-08-26"), eur: 6_142.65 },
        { t: t("2026-08-27"), eur: 6_143.0, cashflow: 5_785.6 },
        { t: t("2026-08-28"), eur: 11_928.25 },
      ],
      "ALL",
    )!;
    expect(Math.abs(r.abs)).toBeLessThan(50); // no phantom ±5,785
    expect(Math.abs(r.pct)).toBeLessThan(5);
  });

  it("null for degenerate input, never a fabricated number", () => {
    expect(rangeReturnCashflowAdjusted([{ t: t("2026-08-01"), eur: 10 }], "ALL")).toBeNull();
    expect(rangeReturnCashflowAdjusted([], "ALL")).toBeNull();
  });
});

describe("changeTone (rc5 R6) — the colour follows the return, not the cash", () => {
  it("a negative return is bearish even when the money delta rounds positive", () => {
    expect(changeTone({ abs: 11_776.22, pct: -0.53 })).toBe("bear");
    expect(changeTone({ abs: -1, pct: 0.4 })).toBe("bull");
    expect(changeTone({ abs: 0, pct: 0 })).toBe("bull"); // flat reads as neutral-positive
    expect(changeTone(null)).toBe("bull");
  });
});
