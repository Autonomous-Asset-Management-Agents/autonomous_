/** STW-2c (#4953) — the request of the follow-up cut's route (plan #4953 §2.1). */
import { describe, it, expect, vi, afterEach } from "vitest";
import { assignPaperStrategy, HttpError } from "@/lib/api";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const respond = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("assignPaperStrategy (#4953)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs konto, basis, acknowledgment and nonce to assign-paper, the name escaped", async () => {
    const answer = { success: true, strategie: "A b", applied: { STOP_LOSS_PCT: "5.0" }, changes: [], detail: "" };
    const fetchMock = respond(201, answer);
    vi.stubGlobal("fetch", fetchMock);
    const basis = { STOP_LOSS_PCT: "6.5" };

    await expect(assignPaperStrategy("A b", "paper2", basis, "ack", "n-1")).resolves.toEqual(answer);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/strategies\/A%20b\/assign-paper$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ konto: "paper2", basis, acknowledgment: "ack", nonce: "n-1" });
  });

  it("a refused request rejects, so nothing is saved after it", async () => {
    vi.stubGlobal("fetch", respond(409, { detail: "nonce_reused" }));
    await expect(assignPaperStrategy("A", "paper1", {}, "ack", "n-2")).rejects.toBeInstanceOf(HttpError);
  });
});
