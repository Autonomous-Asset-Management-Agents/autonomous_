import { describe, it, expect } from "vitest";
import { partitionRoundTable, type RoundTableSenator } from "../console/live/roundTable";

/**
 * #3094 — the console must split the board into DIRECTION (counted in the
 * consensus mean) vs SIZING & RISK (not in the vote: the IV-driven VIXAware
 * sizer + the base conditioner/veto-guard). `partitionRoundTable` is the pure
 * seam; it anchors to the authoritative served consensus so BOTH the fixed
 * record shape (VIXAware role "sizer", weight 0) AND legacy records (VIXAware
 * still role "directional", weight 0.45) land VIXAware in Sizing & Risk — while
 * a genuine flag-OFF directional VIXAware stays in Direction.
 */

const sen = (
  name: string,
  conviction: number,
  weight: number,
  role: string,
  vote: RoundTableSenator["vote"] = "ABSTAIN",
): RoundTableSenator => ({ name, vote, conviction, weight, reasoning: "", hardVeto: false, role });

// Six directional voters → weighted mean 0.6786 (the gate value WITHOUT VIXAware).
const DIRECTIONAL = [
  sen("LSTMSignalAgent", 0.9742, 0.4, "directional", "BULL"),
  sen("MomentumAgent", 0.7981, 0.45, "directional", "BULL"),
  sen("FundamentalsAgent", 0.9, 0.35, "directional", "BULL"),
  sen("SpecialistAlphaAgent", 0.5, 0.4, "directional", "ABSTAIN"),
  sen("NewsSentimentAgent", 0.5494, 0.35, "directional", "ABSTAIN"),
  sen("ValuationAgent", 0.3, 0.35, "directional", "BEAR"),
];
const CONDITIONERS = [
  sen("RegimeDetectionAgent", 0.765, 0.0, "conditioner"),
  sen("DrawdownGuardAgent", 0.73, 0.0, "veto_guard"),
];
const DORMANT = [
  sen("RLConfidenceAgent", 0.9677, 0.0, "directional", "BULL"),
  sen("UpsideSkewAgent", 0.5, 0.0, "directional", "ABSTAIN"),
];
const CONSENSUS_WITHOUT_VIX = 0.6786;

const names = (arr: RoundTableSenator[]) => arr.map((s) => s.name);

describe("partitionRoundTable", () => {
  it("fixed record: VIXAware role 'sizer' (weight 0) → Sizing & Risk, not Direction", () => {
    const vix = sen("VIXAwareRiskAgent", 0.1193, 0.0, "sizer", "BEAR");
    const board = [...DIRECTIONAL, vix, ...CONDITIONERS, ...DORMANT];

    const { direction, sizingRisk, dormant, wSum, wTot } = partitionRoundTable(board, CONSENSUS_WITHOUT_VIX);

    expect(names(direction).sort()).toEqual(names(DIRECTIONAL).sort());
    expect(names(direction)).not.toContain("VIXAwareRiskAgent");
    expect(names(sizingRisk)).toContain("VIXAwareRiskAgent");
    expect(names(sizingRisk)).toContain("RegimeDetectionAgent");
    expect(names(sizingRisk)).toContain("DrawdownGuardAgent");
    expect(names(dormant).sort()).toEqual(["RLConfidenceAgent", "UpsideSkewAgent"]);
    // Σ over Direction reproduces the served consensus (denominator excludes VIX's 0.45).
    expect(wTot).toBeCloseTo(2.3, 5);
    expect(wSum / wTot).toBeCloseTo(CONSENSUS_WITHOUT_VIX, 3);
  });

  it("legacy record: VIXAware still role 'directional' weight 0.45 → anchor moves it to Sizing & Risk", () => {
    const vix = sen("VIXAwareRiskAgent", 0.1193, 0.45, "directional", "BEAR");
    const board = [...DIRECTIONAL, vix, ...CONDITIONERS];

    const { direction, sizingRisk, wTot } = partitionRoundTable(board, CONSENSUS_WITHOUT_VIX);

    expect(names(direction)).not.toContain("VIXAwareRiskAgent");
    expect(names(sizingRisk)).toContain("VIXAwareRiskAgent");
    expect(wTot).toBeCloseTo(2.3, 5); // VIX's 0.45 is NOT in the denominator
  });

  it("flag OFF: VIXAware is a genuine directional voter → stays in Direction", () => {
    const vix = sen("VIXAwareRiskAgent", 0.1193, 0.45, "directional", "BEAR");
    const board = [...DIRECTIONAL, vix, ...CONDITIONERS];
    // Served consensus now INCLUDES VIXAware (flag off → it votes on direction).
    const withVix = (1.560815 + 0.1193 * 0.45) / (2.3 + 0.45);

    const { direction, sizingRisk } = partitionRoundTable(board, withVix);

    expect(names(direction)).toContain("VIXAwareRiskAgent");
    expect(names(sizingRisk)).not.toContain("VIXAwareRiskAgent");
    expect(names(sizingRisk).sort()).toEqual(["DrawdownGuardAgent", "RegimeDetectionAgent"]);
  });

  it("no consensus anchor → falls back to the role tag only", () => {
    const vix = sen("VIXAwareRiskAgent", 0.1193, 0.0, "sizer", "BEAR");
    const { direction, sizingRisk } = partitionRoundTable([...DIRECTIONAL, vix], undefined);
    expect(names(sizingRisk)).toEqual(["VIXAwareRiskAgent"]);
    expect(direction).toHaveLength(DIRECTIONAL.length);
  });
});
