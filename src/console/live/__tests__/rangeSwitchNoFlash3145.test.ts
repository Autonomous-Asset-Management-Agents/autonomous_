// #3145 bug 1 (02.09.): switching 1D -> 1W flashed a DIFFERENT chart with DIFFERENT values for
// about a second. Cause: useIntradayEquity kept ONE curve in state and its effect cleanup reset
// it to [] on every range change, so selectEquityView fell back to the DAILY curve (other
// resolution, other endpoints, other hero delta) until the new fetch resolved — and the
// persisted intraday cache was only ever read in the useState initializer, i.e. on first mount.
// Under test: the per-range curve store keeps each range's last-known curve and seeds a
// never-seen range from the persisted cache, so a switch repaints with the right data at once.
import { describe, it, expect, beforeEach } from "vitest";
import { pickIntradayCurve } from "../intradayEquity";
import { writeIntradayCache } from "../equityCache";

const pt = (iso: string, eur: number) => ({ t: new Date(iso), eur, cashflow: 0 });

const DAY = [pt("2026-09-02T13:30:00Z", 11_800), pt("2026-09-02T19:00:00Z", 11_812)];
const WEEK = [pt("2026-08-27T13:30:00Z", 6_143), pt("2026-09-02T19:00:00Z", 11_812)];

describe("pickIntradayCurve (#3145) — no flash of foreign data on a range switch", () => {
  beforeEach(() => localStorage.clear());

  it("keeps each range's own curve — switching back shows it immediately", () => {
    const store = { "1D": DAY, "1W": WEEK };
    expect(pickIntradayCurve("1D", store)).toEqual(DAY);
    expect(pickIntradayCurve("1W", store)).toEqual(WEEK);
  });

  it("seeds a range that was never fetched in this session from the persisted cache", () => {
    writeIntradayCache("1W", WEEK);
    const curve = pickIntradayCurve("1W", { "1D": DAY });
    expect(curve.map((p) => p.eur)).toEqual([6_143, 11_812]);
  });

  it("never returns another range's curve as a stand-in", () => {
    const curve = pickIntradayCurve("1W", { "1D": DAY });
    expect(curve).toEqual([]); // no cache, no data -> empty, so the caller fails soft ONCE
  });

  it("a one-point curve is not usable and must not be served", () => {
    const curve = pickIntradayCurve("1D", { "1D": [pt("2026-09-02T13:30:00Z", 11_800)] });
    expect(curve).toEqual([]);
  });

  it("long ranges never take an intraday curve", () => {
    expect(pickIntradayCurve("1M", { "1D": DAY, "1W": WEEK })).toEqual([]);
  });
});
