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

/**
 * Max drawdown computed on a DEPOSIT-NEUTRAL equity curve: each point's external cash flow
 * (deposit +, withdrawal −) is stripped by subtracting the running cumulative flow, so a deposit no
 * longer lifts the peak (masking a real slide) and a withdrawal no longer reads as a drawdown. With
 * no cash flows this is byte-identical to `computeMaxDrawdown`. Consistent with the deposit-neutral
 * "Since inception" (TWR) figure — #overview-ia.
 */
export function computeMaxDrawdownCashflowAdjusted(
  curve: { eur: number; cashflow?: number }[],
): number {
  // #3054-fix: a settlement-lagged deposit (its cash-flow date precedes the day equity reflects it)
  // must not be subtracted before the equity jump — that drives the adjusted curve deeply negative
  // and fabricates a thousands-of-percent drawdown (the live −2,444 %). Defer such a flow until
  // equity includes it, mirroring the engine's carry-forward guard (perf_metrics.py).
  // ADR-R16-Zwilling (#3049 Defekt A, mirrors engine perf_metrics.py): the recorded
  // flow is the GROSS transfer; equity only receives the NET credit (international
  // wire fee 1.5 % capped $40 + FX rounding). Subtracting the gross amount from the
  // tiny deposit-adjusted base fabricated the −96.6 % drawdown card (live 28.08.).
  // A fee-plausible overshoot is clamped to the equity-effective amount; anything
  // larger stays an honest loss (no silent beautification).
  const FUNDING_FEE_PCT = 0.015;
  const FUNDING_FEE_CAP_USD = 40.0;
  const FUNDING_FEE_TOL_USD = 2.0;
  let cum = 0;
  let carry = 0;
  const adjusted = curve.map((p, i) => {
    let flow = (p.cashflow ?? 0) + carry;
    if (flow !== 0 && i > 0) {
      const delta = p.eur - curve[i - 1].eur;
      const overshoot =
        flow > 0 ? flow - Math.max(delta, 0) : flow - Math.min(delta, 0);
      const feeBound =
        Math.min(FUNDING_FEE_PCT * Math.abs(flow), FUNDING_FEE_CAP_USD) +
        FUNDING_FEE_TOL_USD;
      if (overshoot > 0 && overshoot <= feeBound) {
        flow -= overshoot; // clamp to the equity-effective amount
      }
    }
    if (p.eur - (cum + flow) <= 0 && flow !== 0) {
      carry = flow; // hold the flow until equity reflects it
      return { eur: p.eur - cum };
    }
    cum += flow;
    carry = 0;
    return { eur: p.eur - cum };
  });
  return computeMaxDrawdown(adjusted);
}
