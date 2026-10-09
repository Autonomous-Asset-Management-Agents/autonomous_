import { describe, it, expect } from "vitest";
import { adaptSpecialistReports, reportBadgeRecommendation } from "../console/live/specialist";
import type { SpecialistReportDTO, SpecialistReportsResponse } from "../lib/api";
import type { SpecialistReport } from "../console/types";

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
    // Legacy wire shape: older engines emitted bare strings — the adapter
    // coerces them; the DTO type documents the CURRENT structured shape.
    const legacy = {
      pros: ["Strong momentum"],
      cons: ["Stretched valuation"],
    } as unknown as Pick<SpecialistReportDTO, "pros" | "cons">;
    const [r] = adaptSpecialistReports(resp({ reports: [dto(legacy)] }));
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

  // The note's own verdict must not be `??`-collapsed with "field absent". An
  // abstaining note (report_recommendation: null) is DISTINCT from the report
  // generator being OFF (field absent -> undefined) — the badge behaves differently.
  it("preserves the note verdict, keeping abstain (null) distinct from absent (undefined)", () => {
    // Report generator ON, note ABSTAINED -> null survives (never coerced to a fake HOLD)
    expect(adaptSpecialistReports(resp({ reports: [dto({ report_recommendation: null })] }))[0].reportRecommendation)
      .toBeNull();
    // Report generator ON, real mid-field HALTEN -> HOLD
    expect(adaptSpecialistReports(resp({ reports: [dto({ report_recommendation: "hold" })] }))[0].reportRecommendation)
      .toBe("HOLD");
    expect(adaptSpecialistReports(resp({ reports: [dto({ report_recommendation: "buy" })] }))[0].reportRecommendation)
      .toBe("BUY");
    // Report generator OFF (field absent) -> undefined, NOT null
    expect(adaptSpecialistReports(resp({ reports: [dto()] }))[0].reportRecommendation)
      .toBeUndefined();
  });
});

// Rich-synthesis card (Direction A): the grounding + vol-band fields map through
// only when present (card flag ON); absent -> honest null/empty (card hidden).
describe("adaptSpecialistReports — synthesis card fields", () => {
  it("maps citations/dropped/grounding_ok/vol band when the card flag is ON", () => {
    const [r] = adaptSpecialistReports(
      resp({
        reports: [
          dto({
            grounding_ok: false,
            synthesis_citations: [
              { kind: "bull", claim: "Record revenue.", headline_index: 1, headline_text: "Revenue record" },
            ],
            synthesis_dropped: [{ kind: "bull", claim: "Cerebras deal.", reason: "entity 'Cerebras' not in any source" }],
            vol_band_low_pct: -8.9,
            vol_band_base_pct: 0.0,
            vol_band_high_pct: 8.9,
            vol_band_sigma_pct: 8.9,
            scenario_basis: "realized_vol_har",
          }),
        ],
      }),
    );
    expect(r.groundingOk).toBe(false); // a real false survives (card present)
    expect(r.synthesisCitations).toHaveLength(1);
    expect(r.synthesisCitations[0].headlineIndex).toBe(1);
    expect(r.synthesisCitations[0].headlineText).toBe("Revenue record");
    expect(r.synthesisDropped[0].reason).toContain("Cerebras");
    expect(r.volBandBasePct).toBe(0.0); // P0-1: a 0.0 base survives
    expect(r.volBandSigmaPct).toBe(8.9);
    expect(r.scenarioBasis).toBe("realized_vol_har");
  });

  it("defaults to hidden-card state when the flag is OFF (fields absent)", () => {
    const [r] = adaptSpecialistReports(resp({ reports: [dto()] }));
    expect(r.groundingOk).toBeNull(); // presence signal: null -> card not rendered
    expect(r.synthesisCitations).toEqual([]);
    expect(r.synthesisDropped).toEqual([]);
    expect(r.volBandSigmaPct).toBeNull();
    expect(r.scenarioBasis).toBeNull();
  });
});

// The bug in one function: a report that abstains must produce NO badge, while a
// real mid-field HALTEN still shows HALTEN. reportBadgeRecommendation is the seam
// the RecPill reads (RecPill returns null -> no pill).
describe("reportBadgeRecommendation", () => {
  const r = (over: Partial<SpecialistReport>): SpecialistReport =>
    ({ symbol: "AAPL", recommendation: "HOLD", ...over }) as SpecialistReport;

  it("shows NOTHING when the note abstained (verdict null), never the fabricated HOLD", () => {
    // This is the reported live bug: recommendation is the uncomputed default "HOLD",
    // but the note abstained -> reportRecommendation is null -> the badge must be null.
    expect(reportBadgeRecommendation(r({ reportRecommendation: null, recommendation: "HOLD" }))).toBeNull();
  });

  it("shows the note's real mid-field HALTEN when it has a rank", () => {
    expect(reportBadgeRecommendation(r({ reportRecommendation: "HOLD" }))).toBe("HOLD");
    expect(reportBadgeRecommendation(r({ reportRecommendation: "BUY" }))).toBe("BUY");
  });

  it("falls back to the specialist recommendation only when the report generator is OFF", () => {
    // field absent (undefined) -> legacy structured-fields reader, no rank verdict exists
    expect(reportBadgeRecommendation(r({ reportRecommendation: undefined, recommendation: "SELL" }))).toBe("SELL");
    expect(reportBadgeRecommendation(r({ reportRecommendation: undefined, recommendation: null }))).toBeNull();
  });
});
