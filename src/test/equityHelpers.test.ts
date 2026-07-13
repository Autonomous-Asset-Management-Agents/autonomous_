import { describe, it, expect } from "vitest";
import { computeMaxDrawdown } from "../console/live/drawdown";
import { filterCurveByRange, rangeReturn } from "../console/live/equityRange";
import { benchmarkTodayPct, computeSharpe, resampleDaily } from "../console/live/metrics";
import { greeting, timeGreeting } from "../console/lib/greeting";
import { adaptEquity } from "../console/live/equity";
import type { BenchmarkEquityResponse } from "../lib/api";

/** G3b (#1050): the pure equity helpers + the /benchmark-equity adapter. */

describe("computeMaxDrawdown", () => {
  it("returns 0 for an empty / single-point curve", () => {
    expect(computeMaxDrawdown([])).toBe(0);
    expect(computeMaxDrawdown([{ eur: 100 }])).toBe(0);
  });
  it("returns 0 for a monotonically rising curve", () => {
    expect(computeMaxDrawdown([{ eur: 100 }, { eur: 110 }, { eur: 120 }])).toBe(0);
  });
  it("computes the deepest peak-to-trough drop as a negative pct", () => {
    // peak 200 → trough 150 = -25%
    expect(computeMaxDrawdown([{ eur: 100 }, { eur: 200 }, { eur: 150 }, { eur: 180 }])).toBeCloseTo(-25);
  });
});

describe("filterCurveByRange", () => {
  const day = (n: number) => new Date(2026, 0, n);
  const curve = Array.from({ length: 40 }, (_, i) => ({ t: day(i + 1), eur: 100 + i }));
  it("ALL returns the whole curve", () => {
    expect(filterCurveByRange(curve, "ALL")).toHaveLength(40);
  });
  it("1W keeps ~7 days anchored on the LAST point (not wall-clock)", () => {
    const out = filterCurveByRange(curve, "1W");
    expect(out.length).toBeLessThan(40);
    expect(out.length).toBeGreaterThanOrEqual(2);
    expect(out[out.length - 1]).toBe(curve[curve.length - 1]);
  });
  it("never returns fewer than 2 points", () => {
    expect(filterCurveByRange(curve, "1D").length).toBeGreaterThanOrEqual(2);
  });
});

describe("rangeReturn", () => {
  const day = (n: number) => new Date(2026, 0, n);
  it("computes abs + pct over the RANGE-FILTERED curve (first -> last), so KPIs track the timeframe", () => {
    // ALL: 100 -> 139 over 40 pts = +39 abs, +39%
    const curve = Array.from({ length: 40 }, (_, i) => ({ t: day(i + 1), eur: 100 + i }));
    const all = rangeReturn(curve, "ALL");
    expect(all?.abs).toBeCloseTo(39);
    expect(all?.pct).toBeCloseTo(39);
  });
  it("respects the range window — YTD is anchored on the last point's year, not the whole curve", () => {
    const curve = [
      { t: new Date(2025, 11, 31), eur: 200 }, // previous year — excluded from YTD
      { t: new Date(2026, 0, 1), eur: 100 }, //   Jan 1 (YTD base)
      { t: new Date(2026, 5, 1), eur: 150 }, //   mid-year
    ];
    const ytd = rangeReturn(curve, "YTD"); // 100 -> 150 = +50%
    expect(ytd?.pct).toBeCloseTo(50);
    expect(ytd?.abs).toBeCloseTo(50);
  });
  it("returns null with <2 points or a zero base (no divide-by-zero / fabricated value)", () => {
    expect(rangeReturn([{ t: day(1), eur: 100 }], "ALL")).toBeNull();
    expect(rangeReturn([{ t: day(1), eur: 0 }, { t: day(2), eur: 5 }], "ALL")).toBeNull();
  });
});

describe("metrics", () => {
  const day = (n: number) => new Date(2026, 0, n, 16);
  it("benchmarkTodayPct uses the last two points", () => {
    expect(benchmarkTodayPct([{ t: day(1), eur: 100 }, { t: day(2), eur: 101 }])).toBeCloseTo(1);
    expect(benchmarkTodayPct([{ t: day(1), eur: 100 }])).toBeNull();
  });
  it("resampleDaily keeps the last point per calendar day", () => {
    const intraday = [
      { t: new Date(2026, 0, 1, 10), eur: 100 },
      { t: new Date(2026, 0, 1, 16), eur: 105 },
      { t: new Date(2026, 0, 2, 16), eur: 110 },
    ];
    const out = resampleDaily(intraday);
    expect(out).toHaveLength(2);
    expect(out[0].eur).toBe(105); // last of day 1
  });
  it("computeSharpe returns null with too little history", () => {
    expect(computeSharpe([{ t: day(1), eur: 100 }, { t: day(2), eur: 101 }])).toBeNull();
  });
});

describe("greeting", () => {
  it("omits the name gracefully", () => {
    expect(greeting()).toMatch(/^Good (morning|afternoon|evening)\.$/);
    expect(greeting("Georg")).toMatch(/, Georg\.$/);
  });
  it("timeGreeting buckets the hour", () => {
    expect(timeGreeting(new Date(2026, 0, 1, 8))).toBe("Good morning");
    expect(timeGreeting(new Date(2026, 0, 1, 14))).toBe("Good afternoon");
    expect(timeGreeting(new Date(2026, 0, 1, 22))).toBe("Good evening");
  });
});

describe("adaptEquity", () => {
  const resp = (over: Partial<BenchmarkEquityResponse> = {}): BenchmarkEquityResponse => ({
    points: [
      { date: "2026-06-10", equity: 100_000 },
      { date: "2026-06-11", equity: 101_000 },
      { date: "2026-06-12", equity: 102_000 },
    ],
    spy_points: [
      { date: "2026-06-10", equity: 5000 },
      { date: "2026-06-12", equity: 5050 },
    ],
    ...over,
  });

  it("maps points/spy_points to {t,eur} curves", () => {
    const v = adaptEquity(resp());
    expect(v.equityCurve).toHaveLength(3);
    expect(v.equityCurve[0].t).toBeInstanceOf(Date);
    expect(v.equityCurve[2].eur).toBe(102_000);
    expect(v.benchmarkCurve).toHaveLength(2);
  });
  it("lastEquity is the prior point's equity (yesterday's close)", () => {
    expect(adaptEquity(resp()).lastEquity).toBe(101_000);
  });
  it("drops malformed points and survives an empty / null response", () => {
    expect(adaptEquity(null).equityCurve).toEqual([]);
    expect(adaptEquity(null).lastEquity).toBeNull();
    const bad = adaptEquity(resp({ points: [{ date: "nope", equity: 1 }] as never }));
    expect(bad.equityCurve).toEqual([]);
  });
});
