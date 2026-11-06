// STW-7 (#4633, ADR-027): pure figures of the live/paper comparison (UX-Konzept #4628, Ablauf 6).
// No React, no side effects.
import type { EquityCurvePoint, EquityView } from "@/console/live/equity";
import { chainLink } from "@/console/shared/twr";
import { computeMaxDrawdownCashflowAdjusted } from "@/console/live/drawdown";
import { benchmarkReturnSinceInception } from "@/console/live/metrics";

export interface CompareWindow {
  /** First common day, 00:00 UTC. */
  from: Date;
  /** Last common day, 00:00 UTC. */
  to: Date;
}

export interface AccountFigures {
  twrPct: number;
  maxDrawdownPct: number;
  /** SPY over the same period; null when the benchmark curve has fewer than two points in it. */
  spyPct: number | null;
}

const DAY_MS = 86_400_000;
const utcDay = (t: Date): number => Math.floor(t.getTime() / DAY_MS) * DAY_MS;

/** The period every account covers: latest first day, earliest last day (UTC calendar days). */
export function commonWindow(views: readonly EquityView[]): CompareWindow | null {
  if (views.length === 0) return null;
  let from = -Infinity, to = Infinity;
  for (const v of views) {
    if (v.equityCurve.length === 0) return null;
    from = Math.max(from, utcDay(v.equityCurve[0].t));
    to = Math.min(to, utcDay(v.equityCurve[v.equityCurve.length - 1].t));
  }
  return to - from < DAY_MS ? null : { from: new Date(from), to: new Date(to) };
}

function cut(curve: readonly EquityCurvePoint[], win: CompareWindow): EquityCurvePoint[] {
  const from = win.from.getTime(), to = win.to.getTime();
  return curve.filter((p) => { const d = utcDay(p.t); return d >= from && d <= to; });
}

/** TWR, max drawdown and SPY of one account over `win`. Cash-flow neutral. */
export function accountMetrics(view: EquityView, win: CompareWindow): AccountFigures | null {
  const equity = cut(view.equityCurve, win);
  if (equity.length < 2) return null;
  const pct = chainLink(equity).pctSeries;
  return {
    twrPct: pct[pct.length - 1],
    maxDrawdownPct: computeMaxDrawdownCashflowAdjusted(equity),
    spyPct: benchmarkReturnSinceInception(cut(view.benchmarkCurve, win)),
  };
}
