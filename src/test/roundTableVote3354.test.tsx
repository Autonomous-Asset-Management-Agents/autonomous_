import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";

/**
 * #3354 — the shared Round-Table vote breakdown, rendered identically on Decisions and Reports.
 * Pins the correctness fixes: overlays are partitioned out of the directional vote (no for/against
 * glyph, not in the tally), dark agents show under "Dormant", and EVERY row uses the agentGlossary
 * label (never the raw class name).
 */
import { RoundTableVote } from "@/console/shared/RoundTableVote";
import {
  directionTally,
  type ConsoleRoundTableDecision,
  type RoundTableSenator,
} from "@/console/live/roundTable";

const sen = (
  o: Partial<RoundTableSenator> & { name: string },
): RoundTableSenator => ({
  vote: "ABSTAIN",
  conviction: 0.5,
  weight: 0,
  reasoning: "",
  hardVeto: false,
  ...o,
});

// Direction mean = (0.88*0.45 + 0.85*0.40 + 0.50*0.35) / 1.20 = 0.7592 → set consensusScore to
// match so partitionRoundTable does no old-record reconciliation.
const decision: ConsoleRoundTableDecision = {
  symbol: "ADI",
  action: "HOLD",
  passed: true,
  conviction: 0.52,
  consensusScore: 0.7592,
  sector: "",
  votesFor: 2, // full-board counts (deliberately NOT what the tally should show)
  votesAbstain: 4,
  votesAgainst: 1,
  vetoReason: "",
  ts: "16:21",
  senators: [
    sen({
      name: "MomentumAgent",
      vote: "BULL",
      conviction: 0.88,
      weight: 0.45,
      role: "directional",
    }),
    sen({
      name: "LSTMSignalAgent",
      vote: "BULL",
      conviction: 0.85,
      weight: 0.4,
      role: "directional",
    }),
    sen({
      name: "ValuationAgent",
      vote: "ABSTAIN",
      conviction: 0.5,
      weight: 0.35,
      role: "directional",
    }),
    sen({
      name: "RegimeDetectionAgent",
      vote: "BULL",
      conviction: 0.76,
      weight: 0,
      role: "conditioner",
    }),
    sen({
      name: "VIXAwareRiskAgent",
      vote: "BEAR",
      conviction: 0.39,
      weight: 0,
      role: "sizer",
    }),
    sen({
      name: "DrawdownGuardAgent",
      vote: "ABSTAIN",
      conviction: 0.65,
      weight: 0,
      role: "veto_guard",
    }),
    sen({
      name: "QualityAgent",
      vote: "ABSTAIN",
      conviction: 0.5,
      weight: 0,
      role: "directional",
    }),
  ],
};

describe("directionTally", () => {
  it("counts only the directional voters (overlays + dark excluded)", () => {
    expect(directionTally(decision)).toEqual({
      votesFor: 2,
      votesAbstain: 1,
      votesAgainst: 0,
    });
  });
});

describe("RoundTableVote (#3354)", () => {
  it("labels every agent via the glossary — never the raw class name", () => {
    const { container } = render(<RoundTableVote decision={decision} />);
    const text = container.textContent ?? "";
    for (const label of [
      "Momentum",
      "LSTM Model",
      "Valuation",
      "Regime Detection",
      "VIX Risk",
      "Drawdown Guard",
      "Quality",
    ]) {
      expect(text).toContain(label);
    }
    for (const raw of [
      "MomentumAgent",
      "QualityAgent",
      "RegimeDetectionAgent",
      "DrawdownGuardAgent",
    ]) {
      expect(text).not.toContain(raw);
    }
  });

  it("partitions overlays into 'Sizing & risk' and dark agents into 'Dormant'", () => {
    const { container } = render(<RoundTableVote decision={decision} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Direction — counted in the");
    expect(text).toContain("Sizing & risk — not in the vote");
    // Dormant uses the glossary label, not the raw name.
    expect(text).toContain("dormant this cycle (weight 0 → no vote): Quality");
  });

  it("shows a directional-only tally (not the mixed full-board count)", () => {
    const { container } = render(<RoundTableVote decision={decision} />);
    const text = container.textContent ?? "";
    expect(text).toContain("2 buy");
    expect(text).toContain("1 hold");
    expect(text).toContain("0 sell");
    expect(text).toContain("directional voters only");
    // The mixed full-board "4 hold" must NOT be what the tally shows.
    expect(text).not.toContain("4 hold");
  });

  it("renders the served consensus headline", () => {
    const { container } = render(<RoundTableVote decision={decision} />);
    expect(container.textContent).toContain("76%"); // round(0.7592*100)
  });

  it("#3369: Sizing & risk rows carry NO directional vote word (— not SELL/BUY)", () => {
    const { container } = render(<RoundTableVote decision={decision} />);
    const text = container.textContent ?? "";
    // Everything from the "Sizing & risk" heading onward is the non-directional section
    // (+ dormant line). The VIX sizer (BEAR) and Regime conditioner (BULL) must NOT render
    // a directional SELL/BUY there — they show "—", so nothing contradicts the "0 sell"
    // tally. (The "SELL ≤ 35%" threshold text lives in the Direction section, before this slice.)
    const sizingOnward = text.slice(text.indexOf("Sizing & risk"));
    expect(sizingOnward.length).toBeGreaterThan(0);
    expect(sizingOnward).not.toContain("SELL");
    expect(sizingOnward).not.toContain("BUY");
    expect(sizingOnward).toContain("—");
    // The directional tally is unchanged.
    expect(text).toContain("0 sell");
  });
});
