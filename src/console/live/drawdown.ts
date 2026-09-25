// src/console/live/drawdown.ts — pure helper: max peak-to-trough drawdown computation.
// No React, no side effects. Tested in the G3b helper tests.

import { chainLink } from "@/console/shared/twr";

/**
 * Compute the maximum peak-to-trough drawdown (as a negative percentage) from
 * an equity curve of { eur } points in chronological order.
 *
 * Algorithm:
 *   Walk forward. Track the running high-water mark. At each point, compute
 *   (current - peak) / peak * 100. The minimum of all those ratios is the max
 *   drawdown (a negative number; e.g. -12.5 means −12.5%).
 *
 * Returns:
 *   - 0 if the curve has fewer than 2 points (no drawdown possible).
 *   - A negative number or 0 when the curve only goes up (peak == current everywhere).
 */
export function computeMaxDrawdown(curve: { eur: number }[]): number {
  if (curve.length < 2) return 0;

  let peak = curve[0].eur;
  let maxDD = 0;

  for (let i = 1; i < curve.length; i++) {
    const current = curve[i].eur;
    if (current > peak) {
      peak = current;
    }
    if (peak !== 0) {
      const dd = ((current - peak) / peak) * 100;
      if (dd < maxDD) maxDD = dd;
    }
  }

  return maxDD;
}

/**
 * #3197: max drawdown on the DEPOSIT-NEUTRAL curve — computed on the chain-linked growth index,
 * the same walk the "autonomous_" return card uses (`chainLink`, shared/twr.ts).
 *
 * The previous version neutralised deposits IN MONEY: `equity − running cumulative flow`. On a
 * funded account that leaves the account's own PRE-funding capital as the base while the real book
 * is many times larger — the live curve of 04.09. kept a base of ~237 $ against an 11.9k book, so a
 * 220 $ slide (−1.85 % of the book) was displayed as **−92.81 %**, right next to a +4.07 % return
 * derived from the very same points. A money-weighted construct cannot sit beside time-weighted
 * ones; the index makes both read the same events.
 *
 * A deposit therefore neither creates nor masks a drawdown, a withdrawal is not a slide, and a
 * real loss after funding is measured against the FUNDED book. With no cash-flows at all the
 * result is identical to `computeMaxDrawdown` on the raw curve.
 */
export function computeMaxDrawdownCashflowAdjusted(
  curve: { eur: number; cashflow?: number }[],
): number {
  if (curve.length < 2) return 0;
  // 1 + r/100 per point: the value of one unit of capital invested at the window's start,
  // unaffected by money entering or leaving.
  const index = chainLink(curve).pctSeries.map((pct) => ({ eur: 1 + pct / 100 }));
  return computeMaxDrawdown(index);
}
