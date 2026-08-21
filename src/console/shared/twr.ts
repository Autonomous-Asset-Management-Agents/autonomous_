/**
 * #2845: cumulative Time-Weighted-Return series for the equity chart.
 *
 * The #2717 series made the KPIs deposit-neutral (engine `perf_metrics.py`), but the chart's
 * %-mode and its range-delta label still used the RAW equity curve — a deposit painted as
 * "+53 % weekly growth". This helper chain-links the per-day sub-period returns EXACTLY like
 * the engine (same timing convention: `cashflow[i]` is the external flow that landed on day
 * `i` and is already contained in `eur[i]`; sub-periods with a non-positive base are skipped,
 * so funding an empty account is not an infinite gain). The inputs are the SAME points the
 * chart plots (`equity.ts` already delivers `cashflow` per point), so chart line, tooltip and
 * delta label can never disagree with each other.
 *
 * Returns one cumulative percent value per point, starting at 0 for the first visible point.
 * Fewer than 2 points → [] for 0 points, [0] for 1 point. Missing cashflow counts as 0.
 */
export interface TwrPoint {
  eur: number;
  cashflow?: number;
}

export function twrSeries(points: readonly TwrPoint[]): number[] {
  if (points.length === 0) return [];
  const out: number[] = [0];
  let growth = 1.0;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1].eur;
    if (prev > 0) {
      const flow = points[i].cashflow ?? 0;
      const marketEquity = points[i].eur - flow; // strip the external flow that landed on day i
      growth *= marketEquity / prev;
    }
    // prev <= 0: no valid base — skip the sub-period, keep chaining (engine semantics).
    out.push((growth - 1.0) * 100.0);
  }
  return out;
}
