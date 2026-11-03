// src/console/live/equityRange.ts — pure helpers for the equity-chart time-range
// toggles (1D/1W/1M/3M/YTD/ALL). No React, no side effects.
// Tested in the G3b helper tests.

import { chainLink } from "@/console/shared/twr";

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
export function filterCurveByRange<T extends { t: Date }>(
  curve: T[],
  range: EquityRange,
  baselineDate?: string | null,
): T[] {
  // #3145: the performance baseline is time zero — no window may reach before it. The engine
  // already slices every served curve there; this keeps the console from widening it again.
  const base = baselineMs(baselineDate);
  if (range === "ALL" || curve.length <= 2) {
    if (base == null) return curve;
    const sliced = curve.filter((p) => p.t.getTime() >= base);
    return sliced.length >= 2 ? sliced : curve.slice(-2);
  }
  const anchor = curve[curve.length - 1].t.getTime();
  let cutoff =
    range === "YTD"
      ? new Date(curve[curve.length - 1].t.getFullYear(), 0, 1).getTime()
      : anchor - WINDOW_DAYS[range] * DAY_MS;
  if (base != null && base > cutoff) cutoff = base;
  const filtered = curve.filter((p) => p.t.getTime() >= cutoff);
  return filtered.length >= 2 ? filtered : curve.slice(-2);
}

/** Parse an ISO baseline to epoch ms; `null` for absent/malformed (fail-soft). */
function baselineMs(baselineDate?: string | null): number | null {
  if (!baselineDate) return null;
  const ms = Date.parse(`${String(baselineDate).slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * #3145: the label for the hero delta. It must state the window that was actually measured —
 * "past week" next to a baseline set yesterday is a false claim, and it is what made the
 * "autonomous_" KPI card (since the baseline) and the hero (past week) look contradictory.
 * When the baseline cuts the range window short, the label names the baseline instead.
 */
export function rangeWindowLabel(
  range: EquityRange,
  baselineDate: string | null | undefined,
  anchor: Date,
): string {
  const base = baselineMs(baselineDate);
  if (base == null) return range === "ALL" ? "since inception" : RANGE_WINDOW_LABEL[range];
  const cutoff =
    range === "ALL"
      ? -Infinity
      : range === "YTD"
        ? new Date(anchor.getFullYear(), 0, 1).getTime()
        : anchor.getTime() - WINDOW_DAYS[range] * DAY_MS;
  if (base <= cutoff) return RANGE_WINDOW_LABEL[range];
  return `since ${new Date(base).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  })}`;
}

const RANGE_WINDOW_LABEL: Record<EquityRange, string> = {
  "1D": "today",
  "1W": "past week",
  "1M": "past month",
  "3M": "past 3 months",
  YTD: "year to date",
  ALL: "since inception",
};

/**
 * Return over the selected range, computed from the RANGE-FILTERED curve endpoints
 * (first -> last) — so the overview KPI cards track the timeframe toggle exactly like
 * the chart does (fixes: at YTD the cards used to still show the daily P/L). Returns
 * null when there are <2 points in the window or a zero base (never a fabricated /
 * divide-by-zero value). `abs` is in the curve's own unit (EUR/USD).
 */
export function rangeReturn<T extends { t: Date; eur: number }>(
  curve: T[],
  range: EquityRange,
): { abs: number; pct: number } | null {
  const f = filterCurveByRange(curve, range);
  if (f.length < 2) return null;
  const start = f[0].eur;
  const end = f[f.length - 1].eur;
  if (!start) return null;
  return { abs: end - start, pct: ((end - start) / start) * 100 };
}

/**
 * #2717: the DEPOSIT-NEUTRAL range change for the Overview hero. Unlike
 * ``rangeReturn`` (raw first→last, which paints a deposit as a gain — the
 * "+54 % past week" bug), this chain-links the per-day sub-period returns like
 * the engine (via ``twrSeries``) so external cash-flows landing inside the window
 * neither inflate nor mask the return. ``abs`` is the market P&L (raw change minus
 * the net flows that landed in the window). Requires ``cashflow`` per point
 * (``equity.ts`` supplies it; missing ⇒ 0). ``null`` for < 2 points in range.
 */
export function rangeReturnCashflowAdjusted<
  T extends { t: Date; eur: number; cashflow?: number },
>(curve: T[], range: EquityRange): { abs: number; pct: number } | null {
  const f = filterCurveByRange(curve, range);
  if (f.length < 2) return null;
  // rc5 R6: money and percent come from the SAME chain-link walk. The old `abs` was
  // end − start − netFlow, an endpoint difference that also counted the sub-periods the
  // walk skips (non-positive base). On a three-day-old account whose 1W window still
  // carries Alpaca's pre-funding zero-equity points that reported the account's entire
  // value as market P&L: "+$11,776.22" beside "−0.53 %" (01.09.).
  const { pctSeries, marketPnl } = chainLink(f);
  return { abs: marketPnl, pct: pctSeries[pctSeries.length - 1] ?? 0 };
}

/**
 * rc5 R6: the hero's colour. It must follow the RETURN, not the money delta — the 01.09.
 * screenshot painted "−0.53 % past week" green because the tone was taken from `abs`.
 * `null`/flat reads as the neutral-positive tone (the UI's default), never as a loss.
 */
export function changeTone(change: { abs: number; pct: number } | null): "bull" | "bear" {
  if (!change) return "bull";
  return change.pct < 0 ? "bear" : "bull";
}
