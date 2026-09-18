// src/console/live/intradayEquity.ts — #2124: pick a real intraday equity curve
// for the Overview's short ranges (1D/1W), falling back to the daily curve
// otherwise or when intraday is unavailable. No React, no side effects.

import { filterCurveByRange, type EquityRange } from "./equityRange";
import type { EquityCurvePoint } from "./equity";
import { readIntradayCache } from "./equityCache";

/** The ranges served by /portfolio-intraday (Alpaca 5Min / 1H). */
const INTRADAY_RANGES: ReadonlySet<EquityRange> = new Set<EquityRange>(["1D", "1W"]);

export function isIntradayRange(range: EquityRange): boolean {
  return INTRADAY_RANGES.has(range);
}

/**
 * Choose the chart source for a range:
 *  - intraday ranges with a usable (>=2 points) intraday curve → the intraday curve
 *  - everything else (long ranges, or intraday empty/unavailable) → the daily curve,
 *    range-filtered exactly as before (fail-soft, byte-identical to today).
 */
export function selectEquityView(
  range: EquityRange,
  intraday: EquityCurvePoint[],
  daily: EquityCurvePoint[],
): EquityCurvePoint[] {
  if (isIntradayRange(range) && intraday.length >= 2) return intraday;
  return filterCurveByRange(daily, range);
}

/** The intraday curves held per range (a range absent from the map was never fetched). */
export type IntradayStore = Partial<Record<EquityRange, EquityCurvePoint[]>>;

/**
 * #3145: the curve to show for `range`, WITHOUT ever borrowing another range's data.
 *
 * The hook used to hold a single curve and reset it to `[]` whenever the range changed, so
 * for the ~1 s until the new fetch resolved the Overview fell back to the DAILY curve — a
 * different resolution with different endpoints, i.e. the chart and the hero delta visibly
 * flashed other values. Each range now keeps its own last-known curve, and a range this
 * session has not fetched yet is seeded from the persisted cache, so a switch repaints with
 * the right data in the same frame.
 *
 * Returns `[]` (never a stand-in) for long ranges, for an unusable <2-point curve, and when
 * neither memory nor cache has anything — the caller then fails soft exactly once.
 */
export function pickIntradayCurve(range: EquityRange, store: IntradayStore): EquityCurvePoint[] {
  if (!isIntradayRange(range)) return [];
  const inMemory = store[range];
  if (inMemory && inMemory.length >= 2) return inMemory;
  const cached = readIntradayCache(range);
  return cached.length >= 2 ? cached : [];
}
