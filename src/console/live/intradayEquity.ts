// src/console/live/intradayEquity.ts — #2124: pick a real intraday equity curve
// for the Overview's short ranges (1D/1W), falling back to the daily curve
// otherwise or when intraday is unavailable. No React, no side effects.

import { filterCurveByRange, type EquityRange } from "./equityRange";
import type { EquityCurvePoint } from "./equity";

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
