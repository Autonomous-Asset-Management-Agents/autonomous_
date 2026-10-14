import { describe, it, expect } from "vitest";
import { humanizeReasoning } from "@/console/shared/reasoning";

/**
 * #3440 — the VIXAware vote row in the per-stock breakdown (Reports + Decisions).
 * Since IMPLIED_VOL_FORECAST_ENABLED default-ON (#3094) the agent reports the name's
 * OWN 30-day implied volatility + cross-section rank, not the market VIX. The old
 * `vix=` parser stopped matching → raw text. These pin the readable IV rendering
 * (per-symbol IV preserved) plus the retained legacy VIX= fallback.
 */
describe("humanizeReasoning — VIXAware IV form (#3440)", () => {
  const REAL =
    "Expected swing for this stock (30-day implied volatility) at 0.2533 — rank 0.070 against the 35 symbols of the previous session (2026-09-11)";

  it("renders per-symbol IV + rank as a readable sentence", () => {
    expect(humanizeReasoning("VIXAwareRiskAgent", REAL)).toBe(
      "Expected swing 25% — calm (7th pct vs. peers).",
    );
  });

  it("bands the rank: moderate and elevated", () => {
    const mod =
      "Expected swing for this stock (30-day implied volatility) at 0.4031 — rank 0.632 against the 35 symbols";
    const hot =
      "Expected swing for this stock (30-day implied volatility) at 0.4462 — rank 0.763 against the 35 symbols";
    expect(humanizeReasoning("VIXAwareRiskAgent", mod)).toBe(
      "Expected swing 40% — moderate (63rd pct vs. peers).",
    );
    expect(humanizeReasoning("VIXAwareRiskAgent", hot)).toBe(
      "Expected swing 45% — elevated (76th pct vs. peers).",
    );
  });

  it("IV without a rank still renders the swing", () => {
    expect(
      humanizeReasoning(
        "VIXAwareRiskAgent",
        "…(30-day implied volatility) at 0.30 (no rank this session)",
      ),
    ).toBe("Expected swing 30% (30-day implied volatility).");
  });

  it("keeps the legacy VIX= fallback (flag OFF → agent emits market VIX)", () => {
    expect(
      humanizeReasoning("VIXAwareRiskAgent", "VIXAware: VIX=18.50 → score=0.55"),
    ).toBe("Volatility calm (VIX 18.5).");
  });

  it("returns null when neither form is present (caller shows de-prefixed raw)", () => {
    expect(humanizeReasoning("VIXAwareRiskAgent", "no volatility data")).toBeNull();
  });

  // --- Review zu #3442 -----------------------------------------------------

  it("FINDING-02: ordinals are English, not 'th' throughout", () => {
    // 1/2/3 take st/nd/rd — und 11/12/13 tun es NICHT. Genau diese Ausnahme
    // uebersieht eine handgeschriebene Fassung.
    const cases: Array<[string, string]> = [
      ["0.010", "1st"],
      ["0.020", "2nd"],
      ["0.030", "3rd"],
      ["0.110", "11th"],
      ["0.120", "12th"],
      ["0.130", "13th"],
      ["0.210", "21st"],
      ["0.220", "22nd"],
      ["0.630", "63rd"],
      ["0.040", "4th"],
    ];
    for (const [rank, erwartet] of cases) {
      const text = `…(30-day implied volatility) at 0.40 — rank ${rank} against 35 symbols`;
      const out = humanizeReasoning("VIXAwareRiskAgent", text);
      expect(out, `rank ${rank}`).toContain(`(${erwartet} pct vs. peers)`);
    }
  });

  it("FINDING-03: a bare '.' never reaches the UI as NaN", () => {
    // `[\d.]+` matchte auch einen einzelnen Punkt; parseFloat(".") ist NaN, und
    // Math.round(NaN * 100) ritt als "NaN%" bis in die Oberflaeche.
    const kaputteIv = "…(30-day implied volatility) at . — rank 0.5 against 35 symbols";
    expect(humanizeReasoning("VIXAwareRiskAgent", kaputteIv)).toBeNull();

    const kaputterRang = "…(30-day implied volatility) at 0.40 — rank . against 35 symbols";
    const out = humanizeReasoning("VIXAwareRiskAgent", kaputterRang);
    expect(out).toBe("Expected swing 40% (30-day implied volatility).");
    expect(out).not.toContain("NaN");
  });

  it("FINDING-03: eine fuehrende Dezimalstelle bleibt gueltig", () => {
    // Die Verschaerfung darf `.25` nicht mit abschneiden — das ist eine echte Zahl.
    expect(
      humanizeReasoning(
        "VIXAwareRiskAgent",
        "…(30-day implied volatility) at .25 (no rank this session)",
      ),
    ).toBe("Expected swing 25% (30-day implied volatility).");
  });
});
