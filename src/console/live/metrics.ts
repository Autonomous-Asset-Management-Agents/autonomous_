// src/console/live/metrics.ts — pure portfolio-metric helpers computed from an
// equity curve. No React, no side effects. Tested in the G3b helper tests.
//
// These exist because the engine exposes no portfolio-level Sharpe endpoint
// (only per-specialist walk-forward Sharpe). We compute an HONEST Sharpe from
// the real equity curve and return null when there isn't enough genuine daily
// history yet — the UI shows "—" rather than a fabricated number.

interface EquityPoint {
  t: Date;
  eur: number;
}

/**
 * Collapse a (possibly intra-day) equity curve to one point per calendar day,
 * keeping the LAST observation of each day. Input is assumed chronological.
 */
export function resampleDaily(curve: EquityPoint[]): EquityPoint[] {
  const byDay = new Map<string, EquityPoint>();
  for (const p of curve) {
    const key = `${p.t.getFullYear()}-${p.t.getMonth()}-${p.t.getDate()}`;
    byDay.set(key, p); // later entries overwrite → last point of the day wins
  }
  return Array.from(byDay.values());
}

/**
 * Annualized Sharpe ratio from daily equity returns (risk-free ≈ 0). Returns
 * null when fewer than `minDays` distinct daily points exist or volatility is
 * zero — callers render "—" in that case rather than a misleading value.
 */
export function computeSharpe(
  curve: EquityPoint[],
  { periodsPerYear = 252, minDays = 20 }: { periodsPerYear?: number; minDays?: number } = {}
): number | null {
  const daily = resampleDaily(curve);
  if (daily.length < minDays) return null;

  const rets: number[] = [];
  for (let i = 1; i < daily.length; i++) {
    const prev = daily[i - 1].eur;
    if (prev > 0) rets.push((daily[i].eur - prev) / prev);
  }
  if (rets.length < 2) return null;

  const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
  const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1);
  const sd = Math.sqrt(variance);
  if (sd === 0) return null;

  return (mean / sd) * Math.sqrt(periodsPerYear);
}

/**
 * Today's benchmark (S&P 500) change in %, derived from the last two points of
 * the benchmark curve. Returns null when there aren't two points to compare.
 */
export function benchmarkTodayPct(benchmark: EquityPoint[]): number | null {
  if (benchmark.length < 2) return null;
  const prev = benchmark[benchmark.length - 2].eur;
  const last = benchmark[benchmark.length - 1].eur;
  if (prev <= 0) return null;
  return ((last - prev) / prev) * 100;
}
