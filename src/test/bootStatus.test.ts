import { describe, it, expect } from "vitest";
import {
  bootStatusLabel,
  provisionStatus,
  applyProvisionLine,
  provisionLabel,
  PROVISION_IDLE,
} from "@/console/splash/bootStatus";

// The BootSplash used to show the RAW last engine-log line under the wordmark (uvicorn/torch/etc.),
// which reads as cryptic system output. bootStatusLabel maps those raw lines to a small set of
// friendly, human phases. The raw log stays available in Settings → Engine-Log and the error tail.

describe("bootStatusLabel — friendly boot phases from raw engine logs", () => {
  it("empty logs → the default starting phase", () => {
    expect(bootStatusLabel([])).toBe("Starting engine…");
  });

  it("model/runtime lines → Loading AI models…", () => {
    expect(bootStatusLabel(["Startup check [ollama]: model 'mistral:7b' ok"])).toBe("Loading AI models…");
    expect(bootStatusLabel(["rl_model_loaded=true, torch warmup done"])).toBe("Loading AI models…");
  });

  it("broker lines → Connecting to your broker…", () => {
    expect(bootStatusLabel(["Alpaca API connected. Status=ACTIVE equity=352.75"])).toBe(
      "Connecting to your broker…",
    );
  });

  it("registry/strategy lines → Warming up the strategy…", () => {
    expect(bootStatusLabel(["Current strategy: RLAgent."])).toBe("Warming up the strategy…");
  });

  it("server-ready lines → Almost ready…", () => {
    expect(bootStatusLabel(["INFO: Uvicorn running on http://127.0.0.1:8001"])).toBe("Almost ready…");
    expect(bootStatusLabel(["Engine: Live strategy and monitor threads started."])).toBe("Almost ready…");
  });

  it("returns the FURTHEST phase reached, not the (possibly generic) last line", () => {
    const logs = [
      "INFO: Started server process [4242]",
      "Startup check [ollama]: model ok",
      "Alpaca API connected. Status=ACTIVE",
      "Current strategy: RLAgent.",
      "Engine: Live strategy and monitor threads started.",
      'INFO: 127.0.0.1:54094 - "GET /health" 200', // generic, most-recent line
    ];
    expect(bootStatusLabel(logs)).toBe("Almost ready…");
  });

  it("first-launch provisioning lines → the download phase, with the latest percent", () => {
    expect(bootStatusLabel(["First launch: fetching the engine runtime (a few minutes, once)."])).toBe(
      "Downloading the engine runtime (once, a few minutes)…",
    );
    expect(bootStatusLabel(["[provisioning] 12% download python", "[provisioning] 43% download python"])).toBe(
      "Downloading the engine runtime (once, a few minutes)… 43%",
    );
  });

  it("the download phase never drags a later engine phase backwards", () => {
    expect(bootStatusLabel(["[provisioning] 100% done", "INFO: Started server process [4242]"])).toBe(
      "Starting engine…",
    );
  });

  it("never leaks a raw log line (always a friendly phrase)", () => {
    const raw = 'INFO: 127.0.0.1:54094 - "GET /benchmark-equity" 200';
    const out = bootStatusLabel([raw]);
    expect(out).not.toContain("127.0.0.1");
    expect(out.endsWith("…")).toBe(true);
  });

  // B4: SHA check and tar extraction report no byte progress, so the percent used to sit frozen at
  // "23%" for minutes. The shell now names those stages; the splash shows a clear text instead.
  it("verify / unpack stages show a clear text instead of a frozen percent", () => {
    expect(bootStatusLabel(["[provisioning] 32% Python-Laufzeit", "[provisioning] 32% Prüfen"])).toBe(
      "Verifying the engine runtime…",
    );
    expect(bootStatusLabel(["[provisioning] 38% Entpacken"])).toBe("Unpacking the engine runtime…");
  });

  it("100% done → the restart notice (the shell relaunches the app itself)", () => {
    expect(bootStatusLabel(["[provisioning] 100% done"])).toBe("Engine runtime ready, restarting once…");
  });
});

// B4: the first-launch provisioning state the splash acts on (no timeout / no Retry while it runs,
// error + Retry only once the shell reports a failure). Derived from the same log lines main.cjs
// emits for every progress tick: "[provisioning] N% stage" and "[provisioning] N% stage — error".
describe("provisionStatus — first-launch runtime provisioning from the shell's log lines", () => {
  it("is idle without provisioning lines (Windows, dev checkout, an already provisioned Mac)", () => {
    expect(provisionStatus([])).toEqual(PROVISION_IDLE);
    expect(provisionStatus(["INFO: Started server process [4242]"])).toEqual(PROVISION_IDLE);
    expect(PROVISION_IDLE.active).toBe(false);
    expect(PROVISION_IDLE.error).toBeNull();
  });

  it("the first-launch announcement starts an active phase without a percent yet", () => {
    expect(provisionStatus(["First launch: fetching the engine runtime (a few minutes, once)."])).toEqual({
      active: true,
      percent: null,
      stage: null,
      error: null,
    });
  });

  it("a progress line carries stage and percent; the latest line wins", () => {
    expect(provisionStatus(["[provisioning] 0% starting", "[provisioning] 23% Python-Laufzeit"])).toEqual({
      active: true,
      percent: 23,
      stage: "Python-Laufzeit",
      error: null,
    });
  });

  it("an error suffix ends the phase with that error (progress.error was set)", () => {
    const s = provisionStatus([
      "[provisioning] 23% Python-Laufzeit",
      "[provisioning] 23% Python-Laufzeit — SHA-256 mismatch for aaagents-python-arm64.tar.gz",
    ]);
    expect(s.active).toBe(false);
    expect(s.error).toBe("SHA-256 mismatch for aaagents-python-arm64.tar.gz");
    expect(s.percent).toBe(23);
    expect(s.stage).toBe("Python-Laufzeit");
  });

  it("'Provisioning failed:' (release lookup failed before any progress tick) ends the phase too", () => {
    const s = provisionStatus([
      "First launch: fetching the engine runtime (a few minutes, once).",
      "Provisioning failed: no macOS release for app version 0.5.1 (tried desktop-mac-v0.5.1-beta)",
    ]);
    expect(s.active).toBe(false);
    expect(s.error).toBe("no macOS release for app version 0.5.1 (tried desktop-mac-v0.5.1-beta)");
  });

  it("100% done and the relaunch notice stay active (the shell restarts the app itself)", () => {
    expect(provisionStatus(["[provisioning] 100% done"])).toEqual({ active: true, percent: 100, stage: "done", error: null });
    expect(
      provisionStatus(["[provisioning] 100% done", "Runtime provisioned — restarting the app once to load it."]).active,
    ).toBe(true);
  });

  it("applyProvisionLine returns the SAME object for unrelated lines (no spurious re-renders)", () => {
    const s = provisionStatus(["[provisioning] 23% Python-Laufzeit"]);
    expect(applyProvisionLine(s, 'INFO: 127.0.0.1:54094 - "GET /health" 200')).toBe(s);
    expect(applyProvisionLine(PROVISION_IDLE, "Startup check [ollama]: model ok")).toBe(PROVISION_IDLE);
  });

  it("provisionLabel: stage and percent for the download, clear texts for verify / unpack / done", () => {
    expect(provisionLabel({ active: true, percent: null, stage: null, error: null })).toBe(
      "Downloading the engine runtime (once, a few minutes)…",
    );
    expect(provisionLabel({ active: true, percent: 43, stage: "Python-Laufzeit", error: null })).toBe(
      "Downloading the engine runtime (once, a few minutes)… 43%",
    );
    expect(provisionLabel({ active: true, percent: 32, stage: "Prüfen", error: null })).toBe("Verifying the engine runtime…");
    expect(provisionLabel({ active: true, percent: 38, stage: "Entpacken", error: null })).toBe("Unpacking the engine runtime…");
    expect(provisionLabel({ active: true, percent: 100, stage: "done", error: null })).toBe("Engine runtime ready, restarting once…");
  });
});
