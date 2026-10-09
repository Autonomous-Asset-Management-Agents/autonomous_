import { describe, it, expect, vi, beforeEach } from "vitest";
import * as api from "../lib/api";

// #2182: liveEnable/liveDisable were sending the body WITHOUT method:"POST", so
// fetchJson spread `{body}` into fetch() → default GET → the Fetch API throws
// `TypeError: Request with GET/HEAD method cannot have body` client-side, the
// request never reached the engine, and the UI showed the generic
// "engine refused or is offline" error. These tests pin the method to POST.

vi.mock("../lib/firebase", () => ({ auth: { currentUser: null } }));

describe("#2182 — live enable/disable must POST", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // A real fetch THROWS on GET+body; assert the method instead of relying on that.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, // RC2-1: 2xx; fetchJson throws on !res.ok
      json: vi.fn().mockResolvedValue({}),
      status: 201,
    });
  });

  it("liveEnable() issues a POST to /api/live/enable with the body", async () => {
    await api.liveEnable("ack-text", "nonce-123");
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = vi.mocked(global.fetch).mock.calls[0];
    expect(String(url)).toContain("/api/live/enable");
    expect((opts as RequestInit).method).toBe("POST");
    expect(String((opts as RequestInit).body)).toContain("ack-text");
    expect(String((opts as RequestInit).body)).toContain("nonce-123");
  });

  it("liveDisable() issues a POST to /api/live/disable", async () => {
    await api.liveDisable("ack-text", "nonce-456");
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = vi.mocked(global.fetch).mock.calls[0];
    expect(String(url)).toContain("/api/live/disable");
    expect((opts as RequestInit).method).toBe("POST");
  });
});
