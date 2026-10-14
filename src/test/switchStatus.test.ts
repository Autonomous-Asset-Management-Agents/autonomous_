// #3168 (02.09.): the Paper↔Live switch screen showed, in practice, ONE message. The phases were
// real but unreachable: `engineServing` is set false at switch start and only re-set by the health
// poll, which runs every 10 s (useHealthPolling.ts) — while the reconcile loop polls /health with
// its own fast backoff and sets `paperTrading` itself. So by the time `engineServing` flipped true,
// "reconciling…" and "switching your account…" had already been passed, and the ~40-60 s engine
// restart sat under a single frozen line.
// Fix under test: (a) the ladder reads the FURTHEST evidence rather than the first failing check —
// the account having flipped proves the engine answered, without waiting for the health poll; and
// (b) the long restart stretch is narrated from the engine's own boot log, the same source the boot
// splash uses.
import { describe, it, expect } from "vitest";
import { switchStatusLabel } from "@/console/desktop/switchStatus";

const base = {
  arriving: false,
  engineServing: false as boolean | null,
  switchReconciling: false,
  paperTrading: true as boolean | null,
  targetPaper: false, // switching TO live
  dataFresh: false,
  bootLogs: [] as string[],
};

describe("switchStatusLabel (#3168) — the restart stretch is narrated", () => {
  it("no logs yet → the direction-flavoured restart line", () => {
    expect(switchStatusLabel(base)).toBe("arming live trading — restarting the engine…");
    expect(switchStatusLabel({ ...base, targetPaper: true, paperTrading: false })).toBe(
      "returning to paper — restarting the engine…",
    );
  });

  it("walks the engine's own boot phases while it comes back up", () => {
    const at = (line: string) => switchStatusLabel({ ...base, bootLogs: [line] });
    expect(at("INFO: Started server process [123]")).toBe(
      "arming live trading — restarting the engine…",
    );
    expect(at("loading model lstm_v3.pt")).toBe("loading AI models…");
    expect(at("alpaca: get_account ok")).toBe("connecting to your broker…");
    expect(at("registry: 12 specialists")).toBe("warming up the strategy…");
    expect(at("Uvicorn running on http://127.0.0.1:8001")).toBe("almost ready…");
  });

  it("never falls back to an earlier phase when a later log line is generic", () => {
    const logs = ["Uvicorn running on ...", "GET /health 200"];
    expect(switchStatusLabel({ ...base, bootLogs: logs })).toBe("almost ready…");
  });
});

describe("switchStatusLabel (#3168) — phases no longer wait on the 10 s health poll", () => {
  it("the account having flipped proves the engine is back, even before engineServing", () => {
    // The real gap: reconcile confirmed live (paperTrading===target) but the health poll has
    // not run yet. Before the fix this still read "restarting the engine…" for up to 10 s.
    const s = switchStatusLabel({
      ...base,
      engineServing: false,
      paperTrading: false, // target reached
      bootLogs: ["Uvicorn running"],
    });
    expect(s).toBe("syncing your live account…");
  });

  it("keeps the reconcile phase reachable while it is genuinely in flight", () => {
    expect(
      switchStatusLabel({ ...base, engineServing: true, switchReconciling: true }),
    ).toBe("reconciling your open positions & orders…");
  });

  it("account not yet the target, reconcile done → the account switch", () => {
    expect(switchStatusLabel({ ...base, engineServing: true })).toBe(
      "switching your account to live…",
    );
  });

  it("target reached and data fresh → finalising, then ready", () => {
    const almost = { ...base, engineServing: true, paperTrading: false, dataFresh: true };
    expect(switchStatusLabel(almost)).toBe("finalising your live account…");
    expect(switchStatusLabel({ ...almost, arriving: true })).toBe("your account is ready");
  });

  it("the ladder only ever moves forward across a realistic switch", () => {
    const steps = [
      { ...base },
      { ...base, bootLogs: ["torch loaded"] },
      { ...base, bootLogs: ["torch loaded", "alpaca get_account"] },
      { ...base, bootLogs: ["torch", "alpaca", "registry"] },
      { ...base, bootLogs: ["uvicorn running"] },
      { ...base, paperTrading: false, bootLogs: ["uvicorn running"] },
      { ...base, engineServing: true, paperTrading: false, dataFresh: true },
      { ...base, arriving: true, engineServing: true, paperTrading: false, dataFresh: true },
    ];
    const seen = steps.map(switchStatusLabel);
    expect(new Set(seen).size).toBe(seen.length); // eight distinct messages, not one
  });
});
