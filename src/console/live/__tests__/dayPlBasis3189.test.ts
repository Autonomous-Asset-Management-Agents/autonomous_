// #3189: the P&L tile measures `equity - lastEquity` against the previous daily CLOSE
// (DailyUpdatesCard.tsx), while the chart/hero measured from the session's first bar — so the
// same day (and the same week) showed two different numbers, and the overnight gap was missing
// from the chart entirely. The engine now prepends Alpaca's `base_value` (the previous close) as
// the curve's first point. This pins the consequence on the console side: the hero's money delta
// over that curve IS the tile's figure, so the two cannot drift apart again.
import { describe, it, expect } from "vitest";
import { rangeReturnCashflowAdjusted } from "../equityRange";

const t = (iso: string) => new Date(iso);

describe("day P&L basis (#3189)", () => {
  it("the hero delta equals equity − previous close, gap included", () => {
    const PREV_CLOSE = 11_800.0;
    const NOW = 11_890.0;
    const curve = [
      { t: t("2026-09-03T13:25:00Z"), eur: PREV_CLOSE }, // engine anchor = base_value
      { t: t("2026-09-03T13:30:00Z"), eur: 11_880.0 }, // gapped-up open
      { t: t("2026-09-03T14:30:00Z"), eur: NOW },
    ];
    const r = rangeReturnCashflowAdjusted(curve, "ALL")!;
    // exactly what the tile computes: equity − lastEquity
    expect(r.abs).toBeCloseTo(NOW - PREV_CLOSE, 6);
    expect(r.pct).toBeCloseTo(((NOW - PREV_CLOSE) / PREV_CLOSE) * 100, 6);
  });

  it("without the anchor the gap is silently lost — the bug, kept as a contrast", () => {
    const openOnly = [
      { t: t("2026-09-03T13:30:00Z"), eur: 11_880.0 },
      { t: t("2026-09-03T14:30:00Z"), eur: 11_890.0 },
    ];
    const r = rangeReturnCashflowAdjusted(openOnly, "ALL")!;
    expect(r.abs).toBeCloseTo(10.0, 6); // +$10 instead of the true +$90
  });

  it("a deposit on the day is still deposit-neutral against the new base", () => {
    const curve = [
      { t: t("2026-09-03T13:25:00Z"), eur: 6_142.65 },
      { t: t("2026-09-03T13:30:00Z"), eur: 6_143.2 },
      { t: t("2026-09-03T14:30:00Z"), eur: 11_928.25, cashflow: 5_785.6 },
    ];
    const r = rangeReturnCashflowAdjusted(curve, "ALL")!;
    expect(Math.abs(r.abs)).toBeLessThan(5); // the transfer is not a gain
    expect(Math.abs(r.pct)).toBeLessThan(1);
  });
});
