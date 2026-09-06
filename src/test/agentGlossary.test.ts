import { describe, it, expect } from "vitest";
import { agentInfo } from "../console/live/agentGlossary";

/**
 * UXC-1 S6 (#3159, Epic #3151) — glossary refresh from AGENT_CATALOG.md.
 * The role texts feed RoundTableView AND the S4 settings card, so they must
 * (a) exist for the full 11-agent roster, (b) state the TRUE consensus role
 * (veto / regime overlay / sizing input are NOT directional votes), and
 * (c) stay neutral/factual (#2866 pattern — no advice, no profiles).
 */

const FALLBACK_ROLE = "One of the Round-Table agents.";

const ROSTER = [
  "DrawdownGuardAgent",
  "SpecialistAlphaAgent",
  "RegimeDetectionAgent",
  "MomentumAgent",
  "VIXAwareRiskAgent",
  "LSTMSignalAgent",
  "RLConfidenceAgent",
  "NewsSentimentAgent",
  "FundamentalsAgent",
  "ValuationAgent",
  "UpsideSkewAgent",
];

describe("agent glossary (UXC-1 S6)", () => {
  it("T1: the deleted PatternRecognitionAgent has no entry any more", () => {
    // #1951 removed the agent from the codebase entirely — a glossary entry would
    // describe a nonexistent voter. The fallback label proves the entry is gone.
    expect(agentInfo("PatternRecognitionAgent").role).toBe(FALLBACK_ROLE);
  });

  it("T2: every agent of the 11-agent roster has a specific entry (no fallback)", () => {
    for (const name of ROSTER) {
      expect(agentInfo(name).role, name).not.toBe(FALLBACK_ROLE);
    }
  });

  it("T3: overlay/sizing agents state their TRUE consensus role — no directional-vote claim", () => {
    const regime = agentInfo("RegimeDetectionAgent");
    expect(`${regime.role} ${regime.criteria}`).toMatch(/excluded from the directional mean/i);
    expect(`${regime.role} ${regime.criteria}`).toMatch(/damp/i);

    const vix = agentInfo("VIXAwareRiskAgent");
    expect(`${vix.role} ${vix.criteria}`).toMatch(/sizing|position size/i);

    const ddg = agentInfo("DrawdownGuardAgent");
    expect(`${ddg.role} ${ddg.criteria}`).toMatch(/veto/i);
    expect(`${ddg.role} ${ddg.criteria}`).not.toMatch(/leans buy/i);
  });

  it("T4: neutrality — no advice, profile or performance wording in any entry", () => {
    const banned = [
      /profil/i,
      /empfohlen/i,
      /recommend/i,
      /aggressiv/i,
      /kapitalerhalt/i,
      /sharpe/i,
      /outperform/i,
      /guarantee/i,
    ];
    for (const name of ROSTER) {
      const info = agentInfo(name);
      const text = `${info.label} ${info.role} ${info.tasks} ${info.criteria}`;
      for (const re of banned) {
        expect(text, `${name}: ${re}`).not.toMatch(re);
      }
      // "drawdown" is allowed ONLY inside the Drawdown Guard's own factual entry.
      if (name !== "DrawdownGuardAgent") {
        expect(text, name).not.toMatch(/drawdown/i);
      }
    }
  });
});
