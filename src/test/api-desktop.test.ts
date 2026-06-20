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
