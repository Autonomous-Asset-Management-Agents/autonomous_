// src/console/live/drawdown.ts — pure helper: max peak-to-trough drawdown computation.
// No React, no side effects. Tested in the G3b helper tests.

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
