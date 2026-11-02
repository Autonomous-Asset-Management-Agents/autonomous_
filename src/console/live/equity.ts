import type { BenchmarkEquityResponse } from "@/lib/api";

/**
 * Equity-curve adapter (G3b, #1050): map the engine's /benchmark-equity
 * response (portfolio `points` + S&P `spy_points`, each `{date, equity}`) to
 * the console's `{t: Date, eur: number}` curve shape that EquityChart and the
 * range/drawdown/metric helpers consume.
 *
 * `lastEquity` = the prior point's equity (yesterday's close), so the Overview
 * can show today's P/L vs `currentEquity` (the latter comes from the portfolio
 * poll). Returns empty curves / null on an empty or malformed response.
 */
export interface EquityCurvePoint {
  t: Date;
  eur: number;
  /** #1957: net external cash-flow on this day (deposit +, withdrawal −), 0/absent when none. */
  cashflow?: number;
}

export interface EquityView {
  equityCurve: EquityCurvePoint[];
  benchmarkCurve: EquityCurvePoint[];
  lastEquity: number | null;
  // #1957: engine-computed, deposit-neutral KPIs (null when the engine did not supply them —
  // e.g. an older engine — so the caller can fail-soft to the legacy calculation).
  twrPct: number | null;
  netDeposits: number | null;
  pnlNetOfDeposits: number | null;
  // #3071: user-chosen baseline (null = true inception) + the account's true inception —
  // the Overview labels its KPI cards from these ("since <date>").
  baselineDate: string | null;
  inceptionDate: string | null;
}

function toCurve(
  points: { date: string; equity: number; cashflow?: number }[] | undefined,
): EquityCurvePoint[] {
  if (!Array.isArray(points)) return [];
  return points
    .map((p) => ({
      t: new Date(p.date),
      eur: typeof p.equity === "number" ? p.equity : NaN,
      cashflow: typeof p.cashflow === "number" ? p.cashflow : 0,
    }))
    .filter((p) => !Number.isNaN(p.t.getTime()) && !Number.isNaN(p.eur));
}

/**
 * #2124: adapt the /portfolio-intraday response ({points:[{date,equity}]}, where
 * `date` is an ISO *datetime*) to the console curve shape. Same mapping as the
 * daily adapter — intraday timestamps just carry a time component, so the curve
 * spreads within a day instead of collapsing to one point.
 */
export function adaptIntraday(
  resp: { points?: { date: string; equity: number }[] } | null | undefined,
): EquityCurvePoint[] {
  return toCurve(resp?.points);
}

export function adaptEquity(resp: BenchmarkEquityResponse | null | undefined): EquityView {
  const equityCurve = toCurve(resp?.points);
  const benchmarkCurve = toCurve(resp?.spy_points);
  const lastEquity =
    equityCurve.length >= 2 ? equityCurve[equityCurve.length - 2].eur : null;
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const iso = (v: unknown): string | null =>
    typeof v === "string" && v.length >= 10 ? v.slice(0, 10) : null;
  return {
    equityCurve,
    benchmarkCurve,
    lastEquity,
    twrPct: num(resp?.twr_pct),
    netDeposits: num(resp?.net_deposits),
    pnlNetOfDeposits: num(resp?.pnl_net_of_deposits),
    baselineDate: iso(resp?.baseline_date),
    inceptionDate: iso(resp?.inception_date),
  };
}
