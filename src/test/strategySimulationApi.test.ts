/** STW-9b (#4917) — client for POST/GET /api/strategy-sim/runs. */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError, getStrategySimulation, startStrategySimulation } from "@/lib/api";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));
const okJson = (status: number, body: unknown) => ({ ok: true, status, statusText: "OK", json: vi.fn().mockResolvedValue(body) });
const call = (i = 0) => vi.mocked(globalThis.fetch).mock.calls[i];

describe("strategy simulation client (#4917)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("POSTs the input as JSON and returns run_id and status", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(okJson(202, { run_id: "0123456789abcdef", status: "laeuft" }));
    const input = { beginn: "2026-01-02", ende: "2026-06-30", strategien: ["A", "B"], startkapital: 1000 };
    expect(await startStrategySimulation(input)).toEqual({ run_id: "0123456789abcdef", status: "laeuft" });
    const [url, init] = call();
    expect(String(url)).toMatch(/\/api\/strategy-sim\/runs$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(input);
  });

  it("GETs the run with an encoded run id", async () => {
    const run = { run_id: "a/b", status: "fertig", ergebnis: { zeitraum: { beginn: "2026-01-02", ende: "2026-06-30" }, strategien: [], unsicherheit: { pfade: 1, handelstage: 3 }, nicht_abgebildet: [] } };
    globalThis.fetch = vi.fn().mockResolvedValue(okJson(200, run));
    expect(await getStrategySimulation("a/b")).toEqual(run);
    expect(String(call()[0])).toMatch(/\/api\/strategy-sim\/runs\/a%2Fb$/);
  });

  it("a 409 tells missing corpus from missing keys by code and params", async () => {
    const body = { detail: "No simulation data for this period", code: "simulation_keys_missing", params: { reason: "keine_keys" } };
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: "Conflict", json: vi.fn().mockResolvedValue(body) });
    const err = await startStrategySimulation({ beginn: "2026-01-02", ende: "2026-06-30", strategien: ["A"], startkapital: 1 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(409);
    expect((err as HttpError).code).toBe("simulation_keys_missing");
    expect((err as HttpError).params).toEqual({ reason: "keine_keys" });
  });

  it("a non-JSON error leaves code and params undefined", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "Not Found", json: vi.fn().mockRejectedValue(new SyntaxError("x")) });
    const err = await getStrategySimulation("0123456789abcdef").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(404);
    expect((err as HttpError).code).toBeUndefined();
    expect((err as HttpError).params).toBeUndefined();
  });
});
