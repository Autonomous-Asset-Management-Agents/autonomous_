import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { RoundTableSenators } from "@/console/shared/RoundTableView";
import { stripAgentPrefix, humanizeReasoning } from "@/console/shared/reasoning";
import type { RoundTableSenator } from "@/console/live/roundTable";

// #2067: the DrawdownGuard "VETO" badge + its raw "peak-to-trough … drawdown=X%"
// reasoning were unreadable and looked like a hard block. The badge now carries an
// explanatory tooltip and the reasoning renders as a plain sentence.

const drawdownVeto: RoundTableSenator = {
  name: "DrawdownGuardAgent",
  vote: "BEAR",
  conviction: 0.0,
  reasoning:
    "DrawdownGuard: VETO=True - peak-to-trough (30d) drawdown=24.12% (peak=746.38, current=566.32) → score=0.000",
  hardVeto: true,
};

describe("RoundTableView — DrawdownGuard veto explanation (#2067)", () => {
  it("renders a plain-language reasoning instead of the raw numbers", () => {
    render(<RoundTableSenators senators={[drawdownVeto]} />);
    expect(screen.getByText(/24% below its 30-day high/i)).toBeTruthy();
    expect(screen.getByText(/not a risk-reducing sell/i)).toBeTruthy();
    // the cryptic raw string is no longer shown to the user
    expect(screen.queryByText(/peak-to-trough/i)).toBeNull();
  });

  it("gives the VETO badge an explanatory tooltip (blocks buys, not sells)", () => {
    render(<RoundTableSenators senators={[drawdownVeto]} />);
    const badge = screen.getByText("VETO");
    const title = badge.getAttribute("title") ?? "";
    expect(title).toMatch(/does NOT block/i);
    expect(title).toMatch(/SELL/i);
  });
  it("drops a non-veto senator's redundant 'AgentName:' prefix (name is already the row label)", () => {
    // SpecialistAlpha has no humanizer → exercises the pure prefix-strip fallback (#2133).
    const normal: RoundTableSenator = {
      name: "SpecialistAlphaAgent",
      vote: "BULL",
      conviction: 0.6,
      reasoning: "SpecialistAlpha: cached analyst report",
      hardVeto: false,
    };
    render(<RoundTableSenators senators={[normal]} />);
    // #2133: display drops the "SpecialistAlpha:" prefix so it no longer reads "SpecialistAlpha … SpecialistAlpha: …"
    expect(screen.getByText("cached analyst report")).toBeTruthy();
    expect(screen.queryByText("SpecialistAlpha: cached analyst report")).toBeNull();
    expect(screen.queryByText("VETO")).toBeNull();
  });
});

describe("stripAgentPrefix (#2133 — no doubled agent name on the Decision page)", () => {
  it("drops a single leading 'AgentName: ' prefix", () => {
    expect(stripAgentPrefix("DrawdownGuard: high=0 invalid, neutral (VETO=False)")).toBe(
      "high=0 invalid, neutral (VETO=False)",
    );
    expect(stripAgentPrefix("LSTMSignal: no registry entry → neutral 0.5")).toBe(
      "no registry entry → neutral 0.5",
    );
  });

  it("handles a parenthetical variant in the prefix", () => {
    expect(stripAgentPrefix("DrawdownGuard (1-Bar Fallback): VETO=False - H=1.00 L=0.90")).toBe(
      "VETO=False - H=1.00 L=0.90",
    );
  });

  it("leaves a prefix-less string untouched", () => {
    expect(stripAgentPrefix("12-1M ret=+3.2%")).toBe("12-1M ret=+3.2%");
  });

  // #2408: producers emit plain-English sentences without an "AgentName: " prefix.
  // A sentence lead-in ending in ":" is NOT an agent prefix — it must survive intact
  // (only single-word CamelCase agent names, optionally "(variant)", are stripped).
  it("keeps a plain-English sentence lead-in ending in ':' (multi-word — not an agent prefix)", () => {
    const regime =
      "Market regime today: bullish — this vote only adjusts how much weight the other voices get, " +
      "it is not a call on this stock (open 100.00 → close 103.00, score 0.650). [src: today's open/close]";
    expect(stripAgentPrefix(regime)).toBe(regime);

    const momentum =
      "Momentum over the past year (excluding the most recent month): +3.1% " +
      "(price 100.00 → 103.10, score 0.552). [src: 12-month price history]";
    expect(stripAgentPrefix(momentum)).toBe(momentum);

    const news =
      "AI news read: sentiment 0.720 on a 0-to-1 scale (0 = very bearish, 1 = very bullish). " +
      "[src: live AI news assessment]";
    expect(stripAgentPrefix(news)).toBe(news);
  });

  it("keeps a lead-in with an apostrophe untouched", () => {
    const move =
      "Today's move: +0.5% (score 0.525). Full price history was unavailable, so this is a " +
      "one-day move only — weak evidence on its own. [src: today's price bar]";
    expect(stripAgentPrefix(move)).toBe(move);
  });
});
describe("humanizeReasoning (#2134 — plain-English Decision-page reasoning)", () => {
  it("RLConfidence → policy + confidence", () => {
    expect(
      humanizeReasoning("RLConfidenceAgent", "RLConfidence: action=buy conf=0.80 → score=0.750"),
    ).toBe("RL policy: BUY (confidence 80%).");
  });

  it("LSTMSignal → price model + action + score", () => {
    expect(
      humanizeReasoning(
        "LSTMSignalAgent",
        "LSTMSignal: pred=+0.120 action=BUY → continuous score=0.630 (0.5+0.5*tanh(pred/1.0))",
      ),
    ).toBe("Price model: BUY (score 0.63).");
  });

  it("Momentum → 12-1M return + tone (main 'ret=' format)", () => {
    expect(
      humanizeReasoning("MomentumAgent", "Momentum: 12-1M ret=+3.20% (p_start=100.00, p_end=103.20) → score=0.55"),
    ).toBe("Momentum +3.2% (bullish).");
  });

  it("VIXAware → volatility regime", () => {
    expect(
      humanizeReasoning("VIXAwareRiskAgent", "VIXAware: VIX=18.50 → score=0.55 (continuous risk scaling)"),
    ).toBe("Volatility calm (VIX 18.5).");
  });

  it("NewsSentiment → tone + score", () => {
    expect(humanizeReasoning("NewsSentimentAgent", "NewsSentiment: LLM → 'bullish on earnings' → score=0.72")).toBe(
      "News sentiment bullish (0.72).",
    );
  });

  it("DrawdownGuard non-veto → below high, within limits", () => {
    expect(
      humanizeReasoning(
        "DrawdownGuardAgent",
        "DrawdownGuard: VETO=False - peak-to-trough (30d) drawdown=3.20% (peak=100.00, current=96.80) → score=0.500",
      ),
    ).toBe("3% below its 30-day high — within limits.");
  });

  it("returns null for unmatched reasoning (caller falls back to the raw string)", () => {
    expect(humanizeReasoning("SpecialistAlphaAgent", "SpecialistAlpha: EXCLUDED — no cached report")).toBeNull();
  });
});
