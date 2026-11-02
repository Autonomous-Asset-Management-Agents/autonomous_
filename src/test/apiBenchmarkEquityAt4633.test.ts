/** STW-7 (#4633) — reading /benchmark-equity from ONE engine of the fleet (#4632) by loopback port. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchBenchmarkEquity, fetchBenchmarkEquityAt } from "@/lib/api";
import { initDesktopBridge, __resetDesktopBridge, getEngineFleet, BRIDGE_KEYS } from "@/lib/desktopBridge";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));
const setBridge = (impl: Record<string, unknown> | undefined) => { (window as unknown as { aaagents?: unknown }).aaagents = impl; };
const okJson = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", json: vi.fn().mockResolvedValue(body) });

describe("fetchBenchmarkEquityAt (#4633)", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    __resetDesktopBridge();
    setBridge({ isDesktop: true, getEngineConnection: vi.fn().mockResolvedValue({ port: 8001, apiKey: "k-session" }) });
    await initDesktopBridge();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => { __resetDesktopBridge(); setBridge(undefined); vi.restoreAllMocks(); });

  it("calls http://127.0.0.1:<port>/benchmark-equity with X-Engine-Key", async () => {
    const body = { points: [{ date: "2026-09-01", equity: 1 }], spy_points: [], twr_pct: 1.5 };
    globalThis.fetch = vi.fn().mockResolvedValue(okJson(body));
    const resp = await fetchBenchmarkEquityAt(8003);
    expect(resp).toEqual(body);
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(String(url)).toBe("http://127.0.0.1:8003/benchmark-equity");
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers["X-Engine-Key"]).toBe("k-session");
    expect(headers["Authorization"]).toBeDefined();
  });

  it("sends an explicit engine key when one is given", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(okJson({ points: [], spy_points: [] }));
    await fetchBenchmarkEquityAt(8002, "k-profile");
    const headers = (vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-Engine-Key"]).toBe("k-profile");
  });

  it("a network error reads as fetch_failed", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await fetchBenchmarkEquityAt(8004)).toEqual({ points: [], spy_points: [], message: "fetch_failed" });
  });

  it.each([403, 500])("HTTP %i reads as fetch_failed", async (status) => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status, statusText: "x", json: vi.fn() });
    expect(await fetchBenchmarkEquityAt(8002)).toEqual({ points: [], spy_points: [], message: "fetch_failed" });
    for (const call of vi.mocked(globalThis.fetch).mock.calls) {
      expect(String(call[0])).toBe("http://127.0.0.1:8002/benchmark-equity");
    }
  });

  it("leaves fetchBenchmarkEquity on the shell's own engine", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(okJson({ points: [], spy_points: [] }));
    await fetchBenchmarkEquity();
    expect(String(vi.mocked(globalThis.fetch).mock.calls[0][0])).toBe("http://127.0.0.1:8001/benchmark-equity");
  });
});

describe("getEngineFleet (#4633)", () => {
  afterEach(() => setBridge(undefined));

  it("is null in the browser and when the shell runs no fleet", async () => {
    setBridge(undefined);
    expect(await getEngineFleet()).toBeNull();
    setBridge({ isDesktop: true, getEngineFleet: vi.fn().mockResolvedValue(null) });
    expect(await getEngineFleet()).toBeNull();
  });

  it("returns the shell's profiles with id, kind and port; malformed entries are dropped", async () => {
    setBridge({
      isDesktop: true,
      getEngineFleet: vi.fn().mockResolvedValue([
        { id: "live", kind: "live", port: 8001, status: "running" },
        { id: "p1", kind: "paper", port: 8002, status: "starting" },
        { id: "bad", kind: "other", port: 8009 },
        { id: "p2", kind: "paper", port: "8003" },
      ]),
    });
    expect(await getEngineFleet()).toEqual([
      { id: "live", kind: "live", port: 8001, status: "running" },
      { id: "p1", kind: "paper", port: 8002, status: "starting" },
    ]);
  });

  it("is null when the IPC fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    setBridge({ isDesktop: true, getEngineFleet: vi.fn().mockRejectedValue(new Error("no handler")) });
    expect(await getEngineFleet()).toBeNull();
  });

  it("is part of the bridge surface the preload must expose", () => {
    expect(BRIDGE_KEYS).toContain("getEngineFleet");
  });
});
