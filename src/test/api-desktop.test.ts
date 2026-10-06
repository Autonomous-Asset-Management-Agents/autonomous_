import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as api from "../lib/api";
import { getApiBase } from "../lib/api";
import { initDesktopBridge, __resetDesktopBridge } from "../lib/desktopBridge";

vi.mock("../lib/firebase", () => ({ auth: { currentUser: null } }));

/**
 * G3 (#1050): in the Electron desktop app every engine call must carry the
 * per-session X-Engine-Key and target the shell's loopback port. The cloud
 * path must be byte-for-byte unchanged when the bridge is absent.
 */

const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

const lastFetchHeaders = () => {
  const call = vi.mocked(global.fetch).mock.calls[0];
  return (call[1] as RequestInit).headers as Record<string, string>;
};
const lastFetchUrl = () => String(vi.mocked(global.fetch).mock.calls[0][0]);

describe("api.ts desktop seam", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetDesktopBridge();
    setBridge(undefined);
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, // RC2-1: fetchJson throws on !res.ok; a 200 stub must set ok
      json: vi.fn().mockResolvedValue({ status: "ok" }),
      status: 200,
    });
  });

  afterEach(() => {
    __resetDesktopBridge();
    setBridge(undefined);
  });

  it("adds X-Engine-Key when the desktop bridge is connected", async () => {
    setBridge({
      isDesktop: true,
      getEngineConnection: vi.fn().mockResolvedValue({ port: 8001, apiKey: "k-xyz" }),
    });
    await initDesktopBridge();

    await api.fetchStrategy();

    expect(lastFetchHeaders()["X-Engine-Key"]).toBe("k-xyz");
  });

  it("targets the shell's loopback port in the desktop app", async () => {
    setBridge({
      isDesktop: true,
      getEngineConnection: vi.fn().mockResolvedValue({ port: 8042, apiKey: "k" }),
    });
    await initDesktopBridge();

    expect(getApiBase()).toBe("http://127.0.0.1:8042");
  });

  it("cloud path is unchanged: no X-Engine-Key header in the browser", async () => {
    await api.fetchStrategy();
    expect(lastFetchHeaders()["X-Engine-Key"]).toBeUndefined();
    // existing OSS auth behavior intact
    expect(lastFetchHeaders()["Authorization"]).toBe("Bearer oss-mode-bypass");
  });
});

describe("sendChat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetDesktopBridge();
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
  });

  it("POSTs the message to /chat and returns the reply", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ reply: "market is open" }),
      status: 200,
    });
    const out = await api.sendChat("is the market open?");
    expect(out).toBe("market is open");
    const [url, opts] = vi.mocked(global.fetch).mock.calls[0];
    expect(String(url)).toMatch(/\/chat$/);
    expect((opts as RequestInit).method).toBe("POST");
  });

  it("returns null when the engine is unreachable", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("refused"));
    expect(await api.sendChat("hi")).toBeNull();
  });
});

describe("fetchJson resilience (RC2-1)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetDesktopBridge();
    setBridge(undefined);
  });
  afterEach(() => {
    __resetDesktopBridge();
    setBridge(undefined);
  });

  it("fail-soft wrapper returns its fallback on a non-2xx (does not leak the error body)", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      statusText: "Forbidden",
      json: vi.fn().mockResolvedValue({ detail: "forbidden" }),
    });
    // fetchStrategy wraps fetchJson in try/catch → null, NOT the {detail} error body.
    expect(await api.fetchStrategy()).toBeNull();
  });

  it("bounds the request with an AbortSignal (so a hung engine can time out)", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: vi.fn().mockResolvedValue({}) });
    await api.fetchStrategy();
    const init = vi.mocked(global.fetch).mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("desktop: a 403 re-resolves the engine key and retries the request ONCE with the fresh key", async () => {
    const getEngineConnection = vi
      .fn()
      .mockResolvedValueOnce({ port: 8001, apiKey: "stale-key" }) // initial cache
      .mockResolvedValueOnce({ port: 8001, apiKey: "fresh-key" }); // resync after the 403
    setBridge({ isDesktop: true, getEngineConnection });
    await initDesktopBridge(); // caches stale-key

    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 403, statusText: "Forbidden", json: vi.fn().mockResolvedValue({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: vi.fn().mockResolvedValue({ strategy: "RLAgent" }) });

    const out = await api.fetchStrategy();
    expect(out).toEqual({ strategy: "RLAgent" });
    expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2);
    const secondHeaders = (vi.mocked(global.fetch).mock.calls[1][1] as RequestInit).headers as Record<string, string>;
    expect(secondHeaders["X-Engine-Key"]).toBe("fresh-key");
    expect(getEngineConnection).toHaveBeenCalledTimes(2);
  });

  it("desktop: retries at most ONCE (a persistent 403 never loops)", async () => {
    setBridge({ isDesktop: true, getEngineConnection: vi.fn().mockResolvedValue({ port: 8001, apiKey: "k" }) });
    await initDesktopBridge();
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 403, statusText: "Forbidden", json: vi.fn().mockResolvedValue({}) });
    expect(await api.fetchStrategy()).toBeNull(); // fail-soft
    expect(vi.mocked(global.fetch)).toHaveBeenCalledTimes(2); // original + exactly one retry, never more
  });
});
