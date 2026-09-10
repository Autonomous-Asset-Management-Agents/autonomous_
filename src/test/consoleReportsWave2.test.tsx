import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Reports } from "../console/desktop/pages/Reports";
import { GroundedSynthesisCard } from "../console/desktop/pages/GroundedSynthesisCard";
import { SymbolLink } from "../console/desktop/SymbolLink";
import { useStore } from "../console/store/useStore";
import type { SpecialistReport } from "../console/types";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";

// REPORTS-UX Wave 2 (#2732): the row's call is the Round-Table decision
// (signal_action), not the specialist recommendation; escalation is a lightning
// bolt, not a grey dot; the confidence column is gone; the header is labelled.
vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
  fetchSpecialistReport: vi.fn().mockResolvedValue({ status: "ok", report: null }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
}));

const report = (over: Partial<SpecialistReport> = {}): SpecialistReport => ({
  symbol: "AAPL",
  recommendation: "HOLD",
  sentimentScore: 5,
  confidence: 0.8,
  escalate: false,
  escalateReason: null,
  companySummary: null,
  investmentThesis: "Thesis.",
  bullCase: null,
  bearCase: null,
  newsSummary: null,
  headlines: [],
  reasons: [],
  edgeSignals: [],
  mlDirection: "up",
  mlConfidence: 0.6,
  mlReturns: { base: 1, bull: 2, bear: -1 },
  signalQuality: "ml_plus_llm",
  walkforwardIc: 0.08,
  walkforwardSharpe: 0.9,
  shortInterestPct: null,
  insiderTradesCount: null,
  politicalTradesCount: null,
  materialEventsCount: null,
  redditMentions: null,
  summary: null,
  pros: [],
  cons: [],
  updatedAt: null,
  groundingOk: true,
  synthesisCitations: [],
  synthesisDropped: [],
  volBandLowPct: null,
  volBandBasePct: null,
  volBandHighPct: null,
  volBandSigmaPct: null,
  scenarioBasis: null,
  lstmXsecPercentile: null,
  lstmXsecRank: null,
  lstmXsecUniverse: null,
  lastClose: null,
  priceBandLow: null,
  priceBandHigh: null,
  chg20dPct: null,
  dist52wHighPct: null,
  rsi14: null,
  macdSignal: null,
  ...over,
});

const rt = (symbol: string, action: "BUY" | "SELL" | "HOLD"): ConsoleRoundTableDecision => ({
  symbol,
  action,
  passed: true,
  conviction: 0,
  sector: "",
  votesFor: 0,
  votesAbstain: 0,
  votesAgainst: 0,
  vetoReason: "",
  ts: "14:35",
  senators: [],
});

describe("Reports — Wave 2 list (Round-Table-authoritative row)", () => {
  beforeEach(() => {
    useStore.setState({ roundTable: [], specialistReports: [], specialistStatus: "", specialistMessage: null });
  });

  it("shows the Round-Table decision as the row call (not the specialist rec) + a labelled header", () => {
    useStore.setState({
      specialistReports: [report({ symbol: "AAPL", recommendation: "HOLD" })],
      roundTable: [rt("AAPL", "SELL")],
    });
    render(<Reports />);
    // labelled column header
    expect(screen.getByText("Round Table")).toBeTruthy();
    expect(screen.getByText(/Last run/i)).toBeTruthy();
    // the call shown is the board's SELL, though the specialist rec is HOLD
    expect(screen.getAllByText("SELL").length).toBeGreaterThan(0);
  });

  it("renders the escalation as a lightning bolt (role=img), never a grey dot", () => {
    useStore.setState({
      specialistReports: [report({ symbol: "AAPL", escalate: true, escalateReason: "Heavy insider activity (4 filings)" })],
      roundTable: [rt("AAPL", "BUY")],
    });
    render(<Reports />);
    // the bolt renders on the row AND (auto-expanded) in the card's Escalated tile.
    expect(screen.getAllByRole("img", { name: /escalated/i }).length).toBeGreaterThan(0);
  });

  it('shows "—" for the call when no board decision exists for the symbol', () => {
    useStore.setState({
      specialistReports: [report({ symbol: "TSLA", recommendation: "BUY" })],
      roundTable: [],
    });
    render(<Reports />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("GroundedSynthesisCard — Round-Table consensus transparency (#2732)", () => {
  const LONG = "UNIQUERATIONALE " + "z".repeat(2900); // ~3000 chars — must not truncate

  const decision = (): ConsoleRoundTableDecision => ({
    symbol: "NVDA",
    action: "BUY",
    passed: true,
    conviction: 0.36, // rebased consensus ~0.68; the card recomputes from weights
    sector: "",
    votesFor: 2,
    votesAbstain: 1,
    votesAgainst: 1,
    vetoReason: "",
    ts: "14:35",
    senators: [
      { name: "Momentum", vote: "BULL", conviction: 0.86, weight: 1.0, reasoning: "breakout", hardVeto: false },
      { name: "LSTM Dynamic", vote: "BULL", conviction: 0.78, weight: 1.2, reasoning: "rank top-1%", hardVeto: false },
      { name: "Specialist Alpha", vote: "ABSTAIN", conviction: 0.5, weight: 0.0, reasoning: "dormant (weight 0)", hardVeto: false },
      { name: "Mean-Reversion", vote: "BEAR", conviction: 0.3, weight: 0.8, reasoning: LONG, hardVeto: false },
    ],
  });

  it("renders consensus = Σ(score×weight)/Σ(weight) over the DIRECTION set, lists weight-0 as dormant, and never truncates a rationale", () => {
    const { container } = render(<GroundedSynthesisCard r={report({ symbol: "NVDA" })} decision={decision()} />);
    const text = container.textContent ?? "";
    // Σ(score×weight) = 0.86 + 0.936 + 0.24 = 2.036 ; Σweight = 3.0 ; consensus = 68%
    expect(text).toMatch(/2\.04/); // Σ(score×weight), 2dp
    expect(text).toMatch(/3\.00/); // Σweight
    expect(text).toMatch(/68%/); // served/rebased consensus
    // the weight-0 agent is listed as dormant (no vote this cycle), not "abstained"
    expect(text).toMatch(/1 dormant/);
    expect(text).toContain("Specialist Alpha");
    // per-agent votes use BUY/HOLD/SELL vocabulary
    expect(screen.getAllByText("BUY").length).toBeGreaterThan(0);
    expect(screen.getByText("SELL")).toBeTruthy();
    // the full ~3000-char rationale renders untruncated
    expect(screen.getByText(new RegExp("UNIQUERATIONALE"))).toBeTruthy();
    expect(text).toContain(LONG);
  });

  // #3094 — the panel must (a) show the AUTHORITATIVE served consensus, not a
  // frontend re-compute that re-includes the excluded sizer, and (b) split the
  // board into Direction vs Sizing & Risk. Fixture = the live 2026-09-08 ANET
  // shape (legacy record: VIXAware still weight 0.45 role "directional" while the
  // gate served the WITHOUT-VIXAware consensus 0.68).
  const anetDecision = (): ConsoleRoundTableDecision => ({
    symbol: "ANET",
    action: "BUY",
    passed: true,
    conviction: 0.6786 * 2 - 1, // signed meter, rebased from the served score
    consensusScore: 0.6786, // AUTHORITATIVE gate value (VIXAware already excluded)
    sector: "",
    votesFor: 3,
    votesAbstain: 2,
    votesAgainst: 2,
    vetoReason: "",
    ts: "15:29",
    senators: [
      { name: "LSTMSignalAgent", vote: "BULL", conviction: 0.9742, weight: 0.4, reasoning: "rank top-1%", hardVeto: false, role: "directional" },
      { name: "MomentumAgent", vote: "BULL", conviction: 0.7981, weight: 0.45, reasoning: "breakout", hardVeto: false, role: "directional" },
      { name: "FundamentalsAgent", vote: "BULL", conviction: 0.9, weight: 0.35, reasoning: "cheap", hardVeto: false, role: "directional" },
      { name: "SpecialistAlphaAgent", vote: "ABSTAIN", conviction: 0.5, weight: 0.4, reasoning: "flat", hardVeto: false, role: "directional" },
      { name: "NewsSentimentAgent", vote: "ABSTAIN", conviction: 0.5494, weight: 0.35, reasoning: "mixed", hardVeto: false, role: "directional" },
      { name: "ValuationAgent", vote: "BEAR", conviction: 0.3, weight: 0.35, reasoning: "rich", hardVeto: false, role: "directional" },
      // legacy shape: still tagged directional + real weight, though the gate excluded it
      { name: "VIXAwareRiskAgent", vote: "BEAR", conviction: 0.1193, weight: 0.45, reasoning: "high implied vol", hardVeto: false, role: "directional" },
    ],
  });

  it("shows the served 68% (NOT the 59% re-compute that re-includes VIXAware) and splits Direction vs Sizing & Risk", () => {
    const { container } = render(<GroundedSynthesisCard r={report({ symbol: "ANET" })} decision={anetDecision()} />);
    const text = container.textContent ?? "";
    // authoritative served consensus, never the with-VIXAware re-compute (~59%)
    expect(text).toMatch(/68%/);
    expect(text).not.toMatch(/59%/);
    // both sections present
    expect(text).toMatch(/Direction — counted in the 68%/);
    expect(text).toMatch(/Sizing & risk — not in the vote/i);
    // VIXAware landed in Sizing & Risk → the DIRECTION Σweight excludes its 0.45
    expect(text).toMatch(/2\.30/); // Σweight over the 6 directional voters
    expect(text).not.toMatch(/2\.75/); // would be the (wrong) with-VIXAware denominator
    expect(text).toContain("VIXAwareRiskAgent");
  });
});

describe("SymbolLink deep-link (Strand B, #2732)", () => {
  beforeEach(() => {
    useStore.setState({ reportSymbol: null, desktopPage: "overview", roundTable: [], specialistReports: [] });
  });

  it("clicking a ticker sets reportSymbol and navigates to Reports", () => {
    render(<SymbolLink symbol="NVDA" />);
    fireEvent.click(screen.getByText("NVDA"));
    expect(useStore.getState().reportSymbol).toBe("NVDA");
    expect(useStore.getState().desktopPage).toBe("reports");
  });

  it("Reports consumes reportSymbol → opens that report expanded, then clears the target", () => {
    useStore.setState({
      specialistReports: [
        report({ symbol: "AAPL" }),
        report({ symbol: "TSLA", investmentThesis: "TSLA-UNIQUE-THESIS marker." }),
      ],
      roundTable: [],
      reportSymbol: "TSLA", // deep-link target
    });
    render(<Reports />);
    // TSLA (not the first row AAPL) is the one expanded — its thesis is in the card.
    expect(screen.getAllByText(/TSLA-UNIQUE-THESIS/i).length).toBeGreaterThan(0);
    // the target is consumed so a later manual collapse still wins.
    expect(useStore.getState().reportSymbol).toBeNull();
  });
});
