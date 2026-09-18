import { describe, it, expect } from "vitest";
import { adaptIntraday } from "../console/live/equity";
import { isIntradayRange, selectEquityView } from "../console/live/intradayEquity";
import type { EquityCurvePoint } from "../console/live/equity";

/** #2124: the Overview uses a real intraday curve for 1D/1W (Alpaca 5Min/1H),
 * falling back to the daily curve otherwise or when intraday is unavailable. */

describe("isIntradayRange", () => {
  it("is true only for the short ranges", () => {
    expect(isIntradayRange("1D")).toBe(true);
    expect(isIntradayRange("1W")).toBe(true);
    for (const r of ["1M", "3M", "YTD", "ALL"] as const) {
      expect(isIntradayRange(r)).toBe(false);
    }
  });
});

describe("adaptIntraday", () => {
  it("maps intraday timestamps to a curve spread WITHIN a day (not one point)", () => {
    const points = Array.from({ length: 12 }, (_, i) => ({
      date: new Date(Date.UTC(2026, 0, 5, 14, i * 5)).toISOString(), // 14:00, 14:05, …
      equity: 10_000 + i * 10,
    }));
    const curve = adaptIntraday({ points });
    expect(curve).toHaveLength(12);
    // real intraday spread: first and last differ by less than a day but by minutes
    const spanMs = curve[curve.length - 1].t.getTime() - curve[0].t.getTime();
    expect(spanMs).toBeGreaterThan(0);
    expect(spanMs).toBeLessThan(86_400_000);
  });
  it("returns an empty curve for an empty/failed response", () => {
    expect(adaptIntraday({ points: [] })).toEqual([]);
  });
});

describe("selectEquityView", () => {
  const day = (n: number) => new Date(2026, 0, n);
  const daily: EquityCurvePoint[] = Array.from({ length: 30 }, (_, i) => ({ t: day(i + 1), eur: 100 + i }));
  const intraday: EquityCurvePoint[] = Array.from({ length: 78 }, (_, i) => ({
    t: new Date(Date.UTC(2026, 0, 30, 13, 0) + i * 300_000),
    eur: 130 + (i % 5),
  }));

  it("uses the intraday curve for 1D when it has >=2 points", () => {
    expect(selectEquityView("1D", intraday, daily)).toHaveLength(78);
  });
  it("falls back to the daily curve for 1D when intraday is empty", () => {
    const out = selectEquityView("1D", [], daily);
    expect(out.length).toBeGreaterThan(0);
    expect(out).not.toBe(intraday);
    // last point is the daily curve's last
    expect(out[out.length - 1].eur).toBe(daily[daily.length - 1].eur);
  });
  it("ignores intraday for long ranges (1M -> daily)", () => {
    const out = selectEquityView("1M", intraday, daily);
    expect(out[0].eur).toBe(daily[0].eur); // daily curve, not the 130-based intraday
  });
});
