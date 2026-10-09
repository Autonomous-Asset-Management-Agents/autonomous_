// #3145 bug 3 (02.09.): "Performance Baseline ist Zeitpunkt 0, ab dem alle berechnungen und
// darstellungen starten." The engine now slices every served curve there (daily + intraday).
// The console must not undo that: a range window may never reach BEFORE the baseline, and the
// hero label must say which window it actually measured — "past week" on a baseline set
// yesterday is a false claim, and it is what made the KPI card and the hero look contradictory.
import { describe, it, expect } from "vitest";
import { filterCurveByRange, rangeWindowLabel } from "../equityRange";

const day = (d: number) => ({ t: new Date(Date.UTC(2026, 7, d, 20)), eur: 1_000 + d });
const CURVE = [day(24), day(25), day(26), day(27), day(28), day(29), day(30), day(31)];

describe("filterCurveByRange with a baseline (#3145)", () => {
  it("never reaches before the baseline", () => {
    const f = filterCurveByRange(CURVE, "1W", "2026-08-29");
    expect(f.every((p) => p.t >= new Date(Date.UTC(2026, 7, 29)))).toBe(true);
  });

  it("without a baseline the window is unchanged", () => {
    expect(filterCurveByRange(CURVE, "1W")).toEqual(filterCurveByRange(CURVE, "1W", null));
  });

  it("keeps two points when the baseline would leave fewer (the chart needs a line)", () => {
    expect(filterCurveByRange(CURVE, "1D", "2026-08-31").length).toBeGreaterThanOrEqual(2);
  });

  it("a baseline older than the curve changes nothing", () => {
    expect(filterCurveByRange(CURVE, "ALL", "2020-01-01")).toEqual(CURVE);
  });
});

describe("rangeWindowLabel (#3145) — the label states the window actually measured", () => {
  const anchor = new Date(Date.UTC(2026, 8, 2, 20)); // 02.09.2026

  it("says 'past week' when the whole week is available", () => {
    expect(rangeWindowLabel("1W", null, anchor)).toBe("past week");
    expect(rangeWindowLabel("1W", "2026-07-01", anchor)).toBe("past week");
  });

  it("names the baseline when it cuts the window short", () => {
    expect(rangeWindowLabel("1W", "2026-09-01", anchor)).toBe("since Sep 1, 2026");
    expect(rangeWindowLabel("1M", "2026-09-01", anchor)).toBe("since Sep 1, 2026");
  });

  it("ALL always reports the baseline when one is set", () => {
    expect(rangeWindowLabel("ALL", "2026-09-01", anchor)).toBe("since Sep 1, 2026");
    expect(rangeWindowLabel("ALL", null, anchor)).toBe("since inception");
  });

  it("fails soft on a malformed baseline", () => {
    expect(rangeWindowLabel("1W", "not-a-date", anchor)).toBe("past week");
  });
});
