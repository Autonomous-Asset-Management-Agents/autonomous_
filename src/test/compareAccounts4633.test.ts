/** STW-7 (#4633) — pure figures of the live/paper comparison (UX-Konzept #4628, Ablauf 6). */
import { describe, it, expect } from "vitest";
import { adaptEquity, type EquityView } from "@/console/live/equity";
import type { BenchmarkEquityResponse } from "@/lib/api";
import { commonWindow, accountMetrics } from "@/console/desktop/strategyWorkshop/compareAccounts";

const day = (iso: string, eur: number, cashflow = 0) => ({ t: new Date(`${iso}T00:00:00Z`), eur, cashflow });
const view = (equity: ReturnType<typeof day>[], spy: ReturnType<typeof day>[] = []): EquityView => ({
  equityCurve: equity, benchmarkCurve: spy, lastEquity: null, twrPct: null, netDeposits: null, pnlNetOfDeposits: null, baselineDate: null, inceptionDate: null,
});
const ymd = (d: Date) => d.toISOString().slice(0, 10);

describe("commonWindow (T1)", () => {
  it("takes the latest first day and the earliest last day", () => {
    const a = view([day("2026-09-01", 100), day("2026-09-10", 101), day("2026-09-20", 102)]);
    const b = view([day("2026-09-05", 100), day("2026-09-15", 99)]);
    const c = view([day("2026-08-20", 100), day("2026-09-18", 103)]);
    const win = commonWindow([a, b, c]);
    expect(win).not.toBeNull();
    expect(ymd(win!.from)).toBe("2026-09-05");
    expect(ymd(win!.to)).toBe("2026-09-15");
  });

  it("works on UTC calendar days, not on the time of day", () => {
    const a = view([{ t: new Date("2026-09-01T23:30:00Z"), eur: 100, cashflow: 0 }, day("2026-09-03", 101)]);
    const b = view([day("2026-09-01", 100), { t: new Date("2026-09-03T05:00:00Z"), eur: 99, cashflow: 0 }]);
    const win = commonWindow([a, b]);
    expect(ymd(win!.from)).toBe("2026-09-01");
    expect(ymd(win!.to)).toBe("2026-09-03");
  });

  it("is null with fewer than two common days", () => {
    const a = view([day("2026-09-01", 100), day("2026-09-05", 101)]);
    const b = view([day("2026-09-05", 100), day("2026-09-09", 101)]);
    expect(commonWindow([a, b])).toBeNull();
    const c = view([day("2026-09-10", 100), day("2026-09-12", 101)]);
    expect(commonWindow([a, c])).toBeNull();
  });

  it("is null without any account or with an empty curve", () => {
    expect(commonWindow([])).toBeNull();
    expect(commonWindow([view([day("2026-09-01", 1), day("2026-09-02", 1)]), view([])])).toBeNull();
  });
});

describe("accountMetrics (T2–T4)", () => {
  it("T2: a deposit inside the period leaves TWR and drawdown unchanged (cash-flow neutral)", () => {
    const win = { from: new Date("2026-09-01T00:00:00Z"), to: new Date("2026-09-05T00:00:00Z") };
    const plain = view([day("2026-09-01", 1000), day("2026-09-02", 1010), day("2026-09-03", 990), day("2026-09-04", 1005), day("2026-09-05", 1020)]);
    const d3 = 990 + 1000, d4 = (d3 * 1005) / 990, d5 = (d4 * 1020) / 1005;
    const funded = view([day("2026-09-01", 1000), day("2026-09-02", 1010), day("2026-09-03", d3, 1000), day("2026-09-04", d4), day("2026-09-05", d5)]);
    const a = accountMetrics(plain, win)!, b = accountMetrics(funded, win)!;
    expect(a.twrPct).toBeCloseTo(2.0, 6);
    expect(b.twrPct).toBeCloseTo(a.twrPct, 6);
    expect(a.maxDrawdownPct).toBeLessThan(0);
    expect(b.maxDrawdownPct).toBeCloseTo(a.maxDrawdownPct, 6);
  });

  it("T3: over the full engine window the TWR equals the engine's twr_pct (±0.01 pp)", () => {
    const dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12"];
    const equity = [10000, 10100, 9950, 10020, 15030, 15200, 14900, 15100, 15350, 15280, 15500, 15620];
    const resp: BenchmarkEquityResponse = {
      points: dates.map((date, i) => ({ date, equity: equity[i], cashflow: i === 4 ? 5000 : 0 })),
      spy_points: dates.map((date, i) => ({ date, equity: 10000 + 10 * i })),
      twr_pct: 4.237258815701961,
      net_deposits: 5000,
    } as BenchmarkEquityResponse;
    const v = adaptEquity(resp), win = commonWindow([v])!, m = accountMetrics(v, win)!;
    expect(Math.abs(m.twrPct - resp.twr_pct!)).toBeLessThanOrEqual(0.01);
  });

  it("T4: the SPY return is cut to the common period, not taken since inception", () => {
    const spy = [day("2026-09-01", 100), day("2026-09-02", 110), day("2026-09-03", 120), day("2026-09-04", 132)];
    const v = view([day("2026-09-01", 1000), day("2026-09-02", 1000), day("2026-09-03", 1000), day("2026-09-04", 1000)], spy);
    const win = { from: new Date("2026-09-03T00:00:00Z"), to: new Date("2026-09-04T00:00:00Z") };
    const m = accountMetrics(v, win)!;
    expect(m.spyPct).toBeCloseTo(10, 6);
    expect(m.twrPct).toBeCloseTo(0, 6);
  });

  it("is null when the account has fewer than two points inside the period", () => {
    const v = view([day("2026-09-01", 1000), day("2026-09-10", 1010)]);
    expect(accountMetrics(v, { from: new Date("2026-09-02T00:00:00Z"), to: new Date("2026-09-09T00:00:00Z") })).toBeNull();
  });

  it("reports SPY as null when the benchmark curve has no two points in the period", () => {
    const v = view([day("2026-09-01", 1000), day("2026-09-02", 1010)]);
    expect(accountMetrics(v, commonWindow([v])!)!.spyPct).toBeNull();
  });
});
