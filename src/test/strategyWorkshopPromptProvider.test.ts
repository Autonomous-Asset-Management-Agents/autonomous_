/**
 * #4940 — the effective provider of the prompt run decides which hint the editor shows.
 * The console never derives the system provider itself; it only reads what the engine
 * reports for the last run (`anbieter`, #4960) and drops it where it cannot be that provider.
 */
import { describe, it, expect } from "vitest";
import {
  effektiverAnbieter,
  systemAnbieterAus,
} from "@/console/desktop/strategyWorkshop/promptProvider";

describe("effektiverAnbieter (#4940)", () => {
  it("system + ollama is local, without web search and so without cost", () => {
    expect(effektiverAnbieter("system", "ollama")).toEqual({ istLokal: true, istCloud: false });
  });

  it("system + gemini is neither: system never searches, whatever it resolves to", () => {
    expect(effektiverAnbieter("system", "gemini")).toEqual({ istLokal: false, istCloud: false });
  });

  it("an explicitly chosen cloud provider is cloud", () => {
    expect(effektiverAnbieter("anthropic", null)).toEqual({ istLokal: false, istCloud: true });
    expect(effektiverAnbieter("gemini", null)).toEqual({ istLokal: false, istCloud: true });
    expect(effektiverAnbieter("openai", "ollama")).toEqual({ istLokal: false, istCloud: true });
  });

  it("system with an unknown provider claims nothing", () => {
    expect(effektiverAnbieter("system", null)).toEqual({ istLokal: false, istCloud: false });
  });

  it("explicit ollama is local", () => {
    expect(effektiverAnbieter("ollama", null)).toEqual({ istLokal: true, istCloud: false });
  });
});

describe("systemAnbieterAus (#4940)", () => {
  it("takes the run's provider while the engine runs on system", () => {
    expect(systemAnbieterAus({ anbieter: "ollama", websuche: false }, "system")).toBe("ollama");
  });

  it("is unknown without a run", () => {
    expect(systemAnbieterAus(null, "system")).toBeNull();
    expect(systemAnbieterAus({ anbieter: null, websuche: null }, "system")).toBeNull();
  });

  it("is unknown while the engine runs on a chosen provider: the run says nothing about system", () => {
    expect(systemAnbieterAus({ anbieter: "anthropic", websuche: true }, "anthropic")).toBeNull();
  });

  it("is unknown after a run with web search: only a chosen provider searches", () => {
    expect(systemAnbieterAus({ anbieter: "gemini", websuche: true }, "system")).toBeNull();
  });
});
