import { describe, it, expect } from "vitest";
import { adaptRoundTableDecisions, actionToVote } from "../console/live/roundTable";
import type { RoundTableDecisionsResponse } from "../lib/api";

/**
 * G3c (#1050): /round-table-decisions → console display shape.
 *
 * The input is the engine's REAL payload — `asdict(SenateSession)`, verified
 * against the round-trip contract test
 * `ai_trading_bot/tests/unit/test_g1b_console_routes.py::test_seeded_store_round_trips`
 * (NOT the orphan fixture, which carried a different shape the engine never
 * emits). Round-Table nomenclature: BUY/SELL/HOLD → BULL/BEAR/ABSTAIN.
 */
const resp = (over: Partial<RoundTableDecisionsResponse> = {}): RoundTableDecisionsResponse => ({
  status: "ok",
  total: 1,
  decisions: [
    {
      symbol: "MSFT",
      signal_action: "BUY",
      consensus_score: 0.62,
      gatekeeper_approved: true,
      gatekeeper_reason: "ok",
      timestamp: "2026-06-12T10:00:00+00:00",
      session_id: "s1",
      votes: [
        { name: "TrendAgent", agent_name: "TrendAgent", score: 0.7, weight: 1.0, reasoning: "up", vetoed: false, signal: "BUY" },
        { name: "RiskAgent", agent_name: "RiskAgent", score: 0.2, weight: 1.0, reasoning: "down", vetoed: true, signal: "SELL" },
        { name: "NewsAgent", agent_name: "NewsAgent", score: 0.5, weight: 0.5, reasoning: "flat", vetoed: false, signal: "HOLD" },
      ],
    },
  ],
  ...over,
});

describe("actionToVote", () => {
  it("maps BUY→BULL, SELL→BEAR, HOLD/anything→ABSTAIN", () => {
    expect(actionToVote("BUY")).toBe("BULL");
    expect(actionToVote("SELL")).toBe("BEAR");
    expect(actionToVote("HOLD")).toBe("ABSTAIN");
    expect(actionToVote("")).toBe("ABSTAIN");
  });
});

describe("adaptRoundTableDecisions (real SenateSession payload)", () => {
  it("derives the vote tally from votes[].signal (engine sends no buy_votes)", () => {
    const [d] = adaptRoundTableDecisions(resp());
    expect(d.votesFor).toBe(1); // one BUY
    expect(d.votesAgainst).toBe(1); // one SELL
    expect(d.votesAbstain).toBe(1); // one HOLD
  });

  it("maps signal_action + senators from votes (agent_name / signal / score / vetoed)", () => {
    const [d] = adaptRoundTableDecisions(resp());
    expect(d.symbol).toBe("MSFT");
    expect(d.action).toBe("BUY");
    expect(d.passed).toBe(true);
    expect(d.ts).toBe("10:00"); // ISO → HH:MM
    expect(d.senators).toHaveLength(3);
    expect(d.senators[0]).toMatchObject({ name: "TrendAgent", vote: "BULL", conviction: 0.7, hardVeto: false });
    expect(d.senators[1]).toMatchObject({ name: "RiskAgent", vote: "BEAR", hardVeto: true });
    expect(d.senators[2].vote).toBe("ABSTAIN");
  });

  it("rebases consensus_score to a signed conviction in [-1,1]; missing → 0 (not masked)", () => {
    expect(adaptRoundTableDecisions(resp({ decisions: [{ symbol: "X", signal_action: "BUY", consensus_score: 1 }] }))[0].conviction).toBeCloseTo(1);
    expect(adaptRoundTableDecisions(resp({ decisions: [{ symbol: "X", signal_action: "SELL", consensus_score: 0 }] }))[0].conviction).toBeCloseTo(-1);
    expect(adaptRoundTableDecisions(resp({ decisions: [{ symbol: "X", signal_action: "HOLD" }] }))[0].conviction).toBeCloseTo(0);
  });

  it("shows the gatekeeper reason only when the decision did NOT pass", () => {
    const passed = adaptRoundTableDecisions(resp({ decisions: [{ symbol: "X", gatekeeper_approved: true, gatekeeper_reason: "ok" }] }));
    expect(passed[0].vetoReason).toBe("");
    const vetoed = adaptRoundTableDecisions(resp({ decisions: [{ symbol: "X", gatekeeper_approved: false, gatekeeper_reason: "max order" }] }));
    expect(vetoed[0].vetoReason).toBe("max order");
  });

  it("survives an empty / null response and missing votes", () => {
    expect(adaptRoundTableDecisions(null)).toEqual([]);
    expect(adaptRoundTableDecisions({ decisions: [] })).toEqual([]);
    const [d] = adaptRoundTableDecisions(resp({ decisions: [{ symbol: "Y", signal_action: "HOLD" }] }));
    expect(d.senators).toEqual([]);
    expect(d.votesFor).toBe(0);
  });

  it("maps the server-joined execution_outcome + reason through (RQ-1 #1516)", () => {
    const [d] = adaptRoundTableDecisions(
      resp({
        decisions: [
          {
            symbol: "X",
            signal_action: "BUY",
            execution_outcome: "blocked:order_value",
            execution_outcome_reason: "Order value limit exceeded.",
          },
        ],
      }),
    );
    expect(d.executionOutcome).toBe("blocked:order_value");
    expect(d.executionOutcomeReason).toBe("Order value limit exceeded.");
  });

  it("leaves executionOutcome undefined when the engine omits it (older engine / HOLD)", () => {
    const [d] = adaptRoundTableDecisions(resp({ decisions: [{ symbol: "X", signal_action: "HOLD" }] }));
    expect(d.executionOutcome).toBeUndefined();
  });
});
