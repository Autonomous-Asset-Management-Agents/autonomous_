import { describe, it, expect } from "vitest";
import { adaptSpecialistReports } from "../console/live/specialist";
import type { SpecialistReportDTO, SpecialistReportsResponse } from "../lib/api";

/**
 * G1b′ / RPAR-#1284: the engine's GET /specialist-reports DTO (snake_case,
 * 0-100 sentiment, lowercase recommendation) → the console's camelCase
 * SpecialistReport view-model. The conversions that a bug hides behind are
 * TDD-pinned here: sentiment rescale, recommendation casing, the 0-survives
 * null-safety contract, the unavailable contract, and pros/cons coercion.
 */
const dto = (over: Partial<SpecialistReportDTO> = {}): SpecialistReportDTO => ({
  symbol: "AAPL",
  sentiment_score: 72,
  recommendation: "buy",
  confidence: 0.81,
  ml_direction: "up",
  ml_base_return_pct: 1.5,
  ...over,
});

const resp = (over: Partial<SpecialistReportsResponse> = {}): SpecialistReportsResponse => ({
  status: "ok",
  total: 1,
  reports: [dto()],
  ...over,
});

describe("adaptSpecialistReports", () => {
  it("rescales sentiment from the engine's 0-100 to the view-model's 0-10", () => {
    const [r] = adaptSpecialistReports(resp());
    expect(r.sentimentScore).toBeCloseTo(7.2); // 72 / 10
  });

  it("uppercases the lowercase engine recommendation (buy → BUY)", () => {
    expect(adaptSpecialistReports(resp())[0].recommendation).toBe("BUY");
    expect(adaptSpecialistReports(resp({ reports: [dto({ recommendation: "sell" })] }))[0].recommendation).toBe("SELL");
    expect(adaptSpecialistReports(resp({ reports: [dto({ recommendation: "hold" })] }))[0].recommendation).toBe("HOLD");
    // unrecognised → null, never masked into a fake HOLD
    expect(adaptSpecialistReports(resp({ reports: [dto({ recommendation: "??" })] }))[0].recommendation).toBeNull();
  });

  it("never `or`-masks a legitimate 0: a 0.0 sentiment / 0 confidence survives", () => {
    const [r] = adaptSpecialistReports(resp({ reports: [dto({ sentiment_score: 0, confidence: 0 })] }));
    expect(r.sentimentScore).toBe(0); // max-bearish, NOT dropped to null
    expect(r.confidence).toBe(0);
  });

  it("maps the registry-off 'unavailable' contract to []", () => {
    expect(adaptSpecialistReports(resp({ status: "unavailable", reports: [] }))).toEqual([]);
    expect(adaptSpecialistReports({ status: "error", reports: [] })).toEqual([]);
    expect(adaptSpecialistReports(null)).toEqual([]);
    expect(adaptSpecialistReports(undefined)).toEqual([]);
  });

  it("coerces pros/cons from a bare string[]", () => {
    const [r] = adaptSpecialistReports(
      resp({ reports: [dto({ pros: ["Strong momentum"], cons: ["Stretched valuation"] })] }),
    );
    expect(r.pros).toEqual([{ text: "Strong momentum", value: "" }]);
    expect(r.cons).toEqual([{ text: "Stretched valuation", value: "" }]);
  });

  it("coerces pros/cons from the structured {text,value}[] shape", () => {
    const [r] = adaptSpecialistReports(
      resp({
        reports: [
          dto({
            pros: [{ text: "Insider buying", value: "3 trades" }],
            cons: [{ text: "Short interest", value: "6.2%" }],
          }),
        ],
      }),
    );
    expect(r.pros).toEqual([{ text: "Insider buying", value: "3 trades" }]);
    expect(r.cons).toEqual([{ text: "Short interest", value: "6.2%" }]);
  });

  it("keeps 'neutral' ML direction as a real verdict, distinct from unavailable", () => {
    expect(adaptSpecialistReports(resp({ reports: [dto({ ml_direction: "neutral" })] }))[0].mlDirection).toBe("neutral");
    expect(adaptSpecialistReports(resp({ reports: [dto({ ml_direction: "unavailable" })] }))[0].mlDirection).toBe("unavailable");
  });
});
