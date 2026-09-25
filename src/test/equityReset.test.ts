import { describe, it, expect } from "vitest";
import { useStore } from "@/console/store/useStore";
import { writeEquityCache, readEquityCache } from "@/console/live/equityCache";

// #2465: on a Paper↔Live switch the stale account's equity curves must be dropped so the
// freshly-switched account never shows the old "since inception" curve (e.g. the €152k paper
// carryover). currentEquity (the live number) is intentionally preserved so the Overview
// "Live history is building" banner can render (paperTrading===false && currentEquity!=null && equityCurve.length===0).
describe("#2465 resetEquityCurves", () => {
  it("clears equityCurve/benchmarkCurve/lastEquity but keeps currentEquity", () => {
    useStore.setState({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      equityCurve: [{ t: "2026-01-01", eur: 152866 }] as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      benchmarkCurve: [{ t: "2026-01-01", eur: 100000 }] as any,
      lastEquity: 152866,
      currentEquity: 224.78,
    });

    useStore.getState().resetEquityCurves();

    const s = useStore.getState();
    expect(s.equityCurve).toEqual([]);
    expect(s.benchmarkCurve).toEqual([]);
    expect(s.lastEquity).toBeNull();
    expect(s.currentEquity).toBe(224.78); // the live equity is NOT cleared
  });

  // The real leak: clearing only the in-memory store is not enough — the PERSISTED localStorage
  // equity cache repaints the old paper ~100K curve on the next Overview mount / renderer reload
  // (→ ~-99% fake loss on the live page). resetEquityCurves must also drop the persisted cache.
  it("also clears the PERSISTED equity cache so it can't repaint the old account", () => {
    writeEquityCache({
      equityCurve: [{ t: new Date("2026-01-01T00:00:00Z"), eur: 100_000 }],
      benchmarkCurve: [{ t: new Date("2026-01-01T00:00:00Z"), eur: 500 }],
      lastEquity: 100_000,
    });
    expect(readEquityCache()).not.toBeNull();

    useStore.getState().resetEquityCurves();

    expect(readEquityCache()).toBeNull();
  });
});

// The stale-account-data fix: at the START of a switch we ALSO clear the account snapshot
// (positions/cash/currentEquity) AND lastSyncAt, so the OLD account's numbers can't linger on the
// target page and no PRE-switch sync counts as "fresh" — the ModeSwitchOverlay holds its pulse until a
// genuine post-switch /portfolio-summary poll lands (lastSyncAt > startedAt).
describe("resetAccountSnapshot (no stale live data across a switch)", () => {
  it("nulls positions/cashEUR/currentEquity and clears lastSyncAt", () => {
    useStore.setState({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      positions: [{ symbol: "HOOD" }] as any,
      cashEUR: 6150,
      currentEquity: 224.78, // the OLD live equity
      lastSyncAt: 1_000_000, // a pre-switch sync
    });

    useStore.getState().resetAccountSnapshot();

    const s = useStore.getState();
    expect(s.positions).toEqual([]);
    expect(s.cashEUR).toBeNull();
    expect(s.currentEquity).toBeNull(); // the stale live number is gone
    expect(s.lastSyncAt).toBeNull(); // no pre-switch sync counts as fresh
  });
});
