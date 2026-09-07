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

/**
 * rc5 R6: the chain-link walk itself. It yields BOTH figures the Overview hero shows —
 * the cumulative percent series AND the market P&L in money — from ONE pass, so the two
 * can never be computed on different bases again. Before this, the hero's money delta was
 * a plain endpoint difference: it still counted the sub-periods this walk deliberately
 * skips (a non-positive base — e.g. the pre-funding zero-equity hours Alpaca returns for
 * `period=1W`), which reported a three-day-old account's entire value as "+$11,776.22"
 * market P&L next to a −0.53 % return.
 *
 * `marketPnl` accumulates `marketEquity − prev` over exactly the counted sub-periods, with
 * the same settlement-lag carry, so sign(marketPnl) === sign(last percent) by construction.
 */
export function chainLink(points: readonly TwrPoint[]): {
  pctSeries: number[];
  marketPnl: number;
} {
  if (points.length === 0) return { pctSeries: [], marketPnl: 0 };
  const out: number[] = [0];
  let growth = 1.0;
  let marketPnl = 0.0;
  // #3054-fix: a flow whose equity impact LAGS (the deposit's account-activity date precedes the
  // day its cash settles into equity) is deferred here — mirrors the engine guard in
  // perf_metrics.compute_cashflow_adjusted_returns. Without it, eur[i] − flow goes negative and the
  // chained TWR explodes (the live −27,833 % week / +32,180 %-style blow-ups).
  let carry = 0.0;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1].eur;
    if (prev > 0) {
      const flow = (points[i].cashflow ?? 0) + carry;
      let marketEquity = points[i].eur - flow; // strip the external flow that landed on day i
      if (flow !== 0) {
        // rc5 R6: is `flow` already contained in eur[i], or does its settlement still lag?
        // The old test — strip unless the remainder goes negative — only caught the extreme
        // case: with prev 6,142.65, a 5,785.60 flow dated one point early and eur[i] 6,143 the
        // remainder is a positive 357.40, so the sub-period read −94.2 % and the next one +94 %
        // (the "−94.27 % today" tile). Decide by the MARKET MOVE each reading implies and take
        // the smaller one: a real move is small next to a funding transfer, so a flow that is
        // not in eur[i] yet reveals itself by implying an enormous one.
        const impliedIfStripped = Math.abs(marketEquity - prev);
        const impliedIfDeferred = Math.abs(points[i].eur - prev);
        if (marketEquity <= 0 || impliedIfDeferred < impliedIfStripped) {
          carry = flow; // settlement lag → treat today flow-free, strip it at the jump
          marketEquity = points[i].eur;
        } else {
          carry = 0.0;
        }
      } else {
        carry = 0.0;
      }
      growth *= marketEquity / prev;
      marketPnl += marketEquity - prev;
    }
    // prev <= 0: no valid base — skip the sub-period, keep chaining (engine semantics).
    // Money follows the same rule: an unmeasurable sub-period contributes no P&L either.
    out.push((growth - 1.0) * 100.0);
  }
  return { pctSeries: out, marketPnl };
}

/** Cumulative TWR series (see `chainLink`) — the chart's %-mode and tooltips. */
export function twrSeries(points: readonly TwrPoint[]): number[] {
  return chainLink(points).pctSeries;
}
