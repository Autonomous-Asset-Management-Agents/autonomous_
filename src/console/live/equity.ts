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
}

export interface EquityView {
  equityCurve: EquityCurvePoint[];
  benchmarkCurve: EquityCurvePoint[];
  lastEquity: number | null;
}

function toCurve(points: { date: string; equity: number }[] | undefined): EquityCurvePoint[] {
  if (!Array.isArray(points)) return [];
  return points
    .map((p) => ({ t: new Date(p.date), eur: typeof p.equity === "number" ? p.equity : NaN }))
    .filter((p) => !Number.isNaN(p.t.getTime()) && !Number.isNaN(p.eur));
}

export function adaptEquity(resp: BenchmarkEquityResponse | null | undefined): EquityView {
  const equityCurve = toCurve(resp?.points);
  const benchmarkCurve = toCurve(resp?.spy_points);
  const lastEquity =
    equityCurve.length >= 2 ? equityCurve[equityCurve.length - 2].eur : null;
  return { equityCurve, benchmarkCurve, lastEquity };
}
