import { describe, it, expect } from "vitest";
import { bootStatusLabel } from "@/console/splash/bootStatus";

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

  it("never leaks a raw log line (always a friendly phrase)", () => {
    const raw = 'INFO: 127.0.0.1:54094 - "GET /benchmark-equity" 200';
    const out = bootStatusLabel([raw]);
    expect(out).not.toContain("127.0.0.1");
    expect(out.endsWith("…")).toBe(true);
  });
});
