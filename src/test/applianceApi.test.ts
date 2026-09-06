// Appliance waitlist API seam — REAL backend contract (tower launch): the seam now
// talks to the `appliance` Cloud Function behind the /api/appliance/* hosting rewrite.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { joinWaitlist, fetchPosition } from "@/lib/applianceApi";

const okJson = (body: unknown) =>
  ({ ok: true, json: async () => body }) as Response;

describe("appliance api seam (Functions backend)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("joinWaitlist POSTs email + ref to /api/appliance/join", async () => {
    vi.mocked(fetch).mockResolvedValue(okJson({ status: "pending" }));
    const r = await joinWaitlist("a@b.de", "cafe1234cafe1234");
    expect(r).toEqual({ status: "pending" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/appliance/join",
      expect.objectContaining({
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: "a@b.de", ref: "cafe1234cafe1234" }),
      }),
    );
  });

  it("joinWaitlist throws on a non-ok response (page shows the error state)", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 500 } as Response);
    await expect(joinWaitlist("a@b.de")).rejects.toThrow();
  });

  it("fetchPosition GETs /api/appliance/position with the code", async () => {
    vi.mocked(fetch).mockResolvedValue(
      okJson({ position: 42, total: 187, referrals: 3, editionSize: 100 }),
    );
    const p = await fetchPosition("cafe1234cafe1234");
    expect(p.position).toBe(42);
    expect(fetch).toHaveBeenCalledWith(
      "/api/appliance/position?code=cafe1234cafe1234",
    );
  });

  it("fetchPosition throws on non-ok (queue block stays hidden, no fake data)", async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 404 } as Response);
    await expect(fetchPosition("cafe1234cafe1234")).rejects.toThrow();
  });
});
