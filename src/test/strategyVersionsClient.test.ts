/** STW-3b (#4863) — GET /api/strategies/{name}/versions: the version list of one strategy. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError, listStrategyVersions } from "@/lib/api";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));
const okJson = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", json: vi.fn().mockResolvedValue(body) });

describe("listStrategyVersions (#4863)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("encodes the name in the path and returns the body unchanged", async () => {
    const body = {
      name: "Wider stops",
      versionen: [
        { hash: "sha256:abcdef0123", hash_kuerzel: "abcdef01", zeitpunkt: "2026-10-28T11:00:00+00:00", aktion: "update", felder: ["STOP_LOSS_PCT"] },
      ],
    };
    globalThis.fetch = vi.fn().mockResolvedValue(okJson(body));
    expect(await listStrategyVersions("Wider stops")).toEqual(body);
    expect(String(vi.mocked(globalThis.fetch).mock.calls[0][0])).toMatch(/\/api\/strategies\/Wider%20stops\/versions$/);
  });

  it("passes a 404 (flag off or unknown strategy) through as HttpError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "Not Found", json: vi.fn() });
    const err = await listStrategyVersions("A").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(404);
  });
});
