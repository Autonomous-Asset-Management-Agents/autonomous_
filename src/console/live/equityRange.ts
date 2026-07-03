// src/console/live/equityRange.ts — pure helpers for the equity-chart time-range
// toggles (1D/1W/1M/3M/YTD/ALL). No React, no side effects. Tested in
// the G3b helper tests.

export type EquityRange = "1D" | "1W" | "1M" | "3M" | "YTD" | "ALL";

/** Display order of the range toggle buttons. */
export const EQUITY_RANGES: EquityRange[] = ["1D", "1W", "1M", "3M", "YTD", "ALL"];

const DAY_MS = 86_400_000;
const WINDOW_DAYS: Record<"1D" | "1W" | "1M" | "3M", number> = {
  "1D": 1,
  "1W": 7,
  "1M": 30,
  "3M": 91,
};

/**
 * Filter a chronological equity curve to the selected range. The window is
 * anchored on the LAST point's timestamp (not wall-clock "now"), so the chart
 * still works when the data lags real time. YTD = since Jan 1 of the last
 * point's year. ALL returns the whole curve.
 *
 * Guarantees at least 2 points back (the chart needs two to draw a line), so a
 * very short window over sparse data never produces an empty frame.
 */
export function filterCurveByRange<T extends { t: Date }>(curve: T[], range: EquityRange): T[] {
  if (range === "ALL" || curve.length <= 2) return curve;
  const anchor = curve[curve.length - 1].t.getTime();
  const cutoff =
    range === "YTD"
      ? new Date(curve[curve.length - 1].t.getFullYear(), 0, 1).getTime()
      : anchor - WINDOW_DAYS[range] * DAY_MS;
  const filtered = curve.filter((p) => p.t.getTime() >= cutoff);
  return filtered.length >= 2 ? filtered : curve.slice(-2);
}
