/** STW-3c (#4890) — a non-2xx engine answer carries the coded error to the caller.
 *  The engine answers `{detail, code, params}` on strategy and settings routes; `fetchJson`
 *  reads the body best-effort and puts those fields on `HttpError`. Plan §2 point 6. */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { deleteStrategy, HttpError, listStrategies } from "@/lib/api";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const antwort = (status: number, statusText: string, json: () => Promise<unknown>) => ({
  ok: false,
  status,
  statusText,
  json: vi.fn(json),
});

async function fehler(promise: Promise<unknown>): Promise<HttpError> {
  try {
    await promise;
  } catch (e) {
    return e as HttpError;
  }
  throw new Error("expected a rejection");
}

describe("HttpError carries code, params and detail (#4890)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("fills code, params and detail from a coded JSON body", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      antwort(422, "Unprocessable Entity", async () => ({
        detail: "At most 3 strategies — delete one first",
        code: "strategy_limit_reached",
        params: { limit: 3 },
      })),
    );
    const err = await fehler(deleteStrategy("D", "ack", "n-1"));
    expect(err).toBeInstanceOf(HttpError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("strategy_limit_reached");
    expect(err.params).toEqual({ limit: 3 });
    expect(err.detail).toBe("At most 3 strategies — delete one first");
    expect(err.message).toBe(
      "Engine request failed: HTTP 422 Unprocessable Entity (/api/strategies/D)",
    );
  });

  it("keeps detail without code for an uncoded framework body", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(antwort(404, "Not Found", async () => ({ detail: "Not Found" })));
    const err = await fehler(listStrategies());
    expect(err.status).toBe(404);
    expect(err.detail).toBe("Not Found");
    expect(err.code).toBeUndefined();
    expect(err.params).toBeUndefined();
  });

  it("a non-JSON body leaves the fields undefined and throws only once", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      antwort(503, "Service Unavailable", async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      }),
    );
    const err = await fehler(listStrategies());
    expect(err).toBeInstanceOf(HttpError);
    expect(err.status).toBe(503);
    expect(err.code).toBeUndefined();
    expect(err.params).toBeUndefined();
    expect(err.detail).toBeUndefined();
    expect(err.message).toBe("Engine request failed: HTTP 503 Service Unavailable (/api/strategies)");
  });

  it("a list detail (framework validation) is not taken as a coded error", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      antwort(422, "Unprocessable Entity", async () => ({ detail: [{ type: "missing" }], code: "x" })),
    );
    const err = await fehler(listStrategies());
    expect(err.status).toBe(422);
    expect(err.detail).toBeUndefined();
    expect(err.code).toBeUndefined();
    expect(err.params).toBeUndefined();
  });

  it("the three-argument constructor still works", () => {
    const err = new HttpError(500, "x", "/api/strategies");
    expect(err.status).toBe(500);
    expect(err.code).toBeUndefined();
  });
});
