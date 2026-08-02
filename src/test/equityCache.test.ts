import { describe, it, expect, beforeEach } from "vitest";
import {
  readEquityCache,
  writeEquityCache,
  readIntradayCache,
  writeIntradayCache,
} from "../console/live/equityCache";
import type { EquityView, EquityCurvePoint } from "../console/live/equity";

// Minimal in-memory localStorage stub so the test is env-independent (node or jsdom).
function installLocalStorage(): void {
  const store = new Map<string, string>();
  (globalThis as unknown as { localStorage: Storage }).localStorage = {
    getItem: (k: string) => (store.has(k) ? (store.get(k) as string) : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => void store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

const view = (): EquityView => ({
  equityCurve: [
    { t: new Date("2026-07-25T14:00:00.000Z"), eur: 10_000 },
    { t: new Date("2026-07-25T14:05:00.000Z"), eur: 10_050 },
  ],
  benchmarkCurve: [
    { t: new Date("2026-07-25T14:00:00.000Z"), eur: 500 },
    { t: new Date("2026-07-25T14:05:00.000Z"), eur: 502 },
  ],
  lastEquity: 10_000,
});

describe("equityCache (PR-A Instant-Data)", () => {
  beforeEach(() => installLocalStorage());

  it("round-trips an EquityView, reviving Date objects", () => {
    writeEquityCache(view());
    const got = readEquityCache();
    expect(got).not.toBeNull();
    expect(got!.equityCurve).toHaveLength(2);
    expect(got!.equityCurve[0].t).toBeInstanceOf(Date);
    expect(got!.equityCurve[0].t.getTime()).toBe(
      new Date("2026-07-25T14:00:00.000Z").getTime(),
    );
    expect(got!.equityCurve[1].eur).toBe(10_050);
    expect(got!.benchmarkCurve[0].eur).toBe(500);
    expect(got!.lastEquity).toBe(10_000);
  });

  it("returns null when nothing is cached", () => {
    expect(readEquityCache()).toBeNull();
  });

  it("returns null on malformed JSON (fail-soft, never throws)", () => {
    localStorage.setItem("aaa.console.equityCache.v1", "{not json");
    expect(() => readEquityCache()).not.toThrow();
    expect(readEquityCache()).toBeNull();
  });

  it("ignores a cache written under a different schema version", () => {
    localStorage.setItem(
      "aaa.console.equityCache.v1",
      JSON.stringify({ v: 999, equityCurve: [], benchmarkCurve: [], lastEquity: null }),
    );
    expect(readEquityCache()).toBeNull();
  });

  it("round-trips an intraday curve per range and revives Dates", () => {
    const curve: EquityCurvePoint[] = [
      { t: new Date("2026-07-25T14:00:00.000Z"), eur: 10_000 },
      { t: new Date("2026-07-25T14:05:00.000Z"), eur: 10_010 },
    ];
    writeIntradayCache("1D", curve);
    const got = readIntradayCache("1D");
    expect(got).toHaveLength(2);
    expect(got[0].t).toBeInstanceOf(Date);
    expect(got[1].eur).toBe(10_010);
    // a different range is independent
    expect(readIntradayCache("1W")).toEqual([]);
  });

  it("returns [] for intraday when absent or malformed (fail-soft)", () => {
    expect(readIntradayCache("1D")).toEqual([]);
    localStorage.setItem("aaa.console.intradayCache.v1.1D", "garbage");
    expect(() => readIntradayCache("1D")).not.toThrow();
    expect(readIntradayCache("1D")).toEqual([]);
  });
});
