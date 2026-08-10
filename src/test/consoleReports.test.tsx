import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Reports } from "../console/desktop/pages/Reports";
import { useStore } from "../console/store/useStore";
import type { SpecialistReport } from "../console/types";

// Reports polls /specialist-reports only now (Round-Table decisions moved to the
// Decisions page, #1435); stub the fetch so the test renders from seeded state.
vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
  // #2710: the row-expand lazy fetch — return null so the reader renders from the
  // seeded list report (the test asserts that report's content).
  fetchSpecialistReport: vi.fn().mockResolvedValue({ status: "ok", report: null }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
}));

const report = (over: Partial<SpecialistReport> = {}): SpecialistReport => ({
  symbol: "AAPL",
  recommendation: "BUY",
  sentimentScore: 7.2,
  confidence: 0.81,
  escalate: false,
  escalateReason: null,
  companySummary: null,
  investmentThesis: "Durable franchise with services momentum.",
  bullCase: null,
  bearCase: null,
  newsSummary: null,
  headlines: [],
  reasons: [],
  edgeSignals: [],
  mlDirection: "up",
  mlConfidence: 0.6,
  mlReturns: { base: 1.5, bull: 3.0, bear: -1.0 },
  signalQuality: "ml_plus_llm",
  walkforwardIc: 0.08,
  walkforwardSharpe: 0.9,
  shortInterestPct: null,
  insiderTradesCount: null,
  politicalTradesCount: null,
  materialEventsCount: null,
  redditMentions: null,
  summary: "Specialist leans bullish on AAPL this cycle.",
  pros: [{ text: "Insider buying cluster", value: "" }],
  cons: [],
  updatedAt: null,
  groundingOk: null,
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

describe("console Reports page (specialist-only)", () => {
  beforeEach(() => {
    useStore.setState({ roundTable: [], specialistReports: [], specialistStatus: "", specialistMessage: null });
  });

  it("shows the empty state for the specialist section", () => {
    render(<Reports />);
    expect(screen.getByText(/no specialist reports yet/i)).toBeTruthy();
  });

  it("surfaces the engine's registry-off message verbatim when present", () => {
    useStore.setState({
      specialistStatus: "unavailable",
      specialistMessage: "StockSpecialistRegistry is not running on this deployment.",
    });
    render(<Reports />);
    expect(screen.getByText(/stockspecialistregistry is not running/i)).toBeTruthy();
  });

  it("renders the selected report's grounded card (rec + thesis content)", () => {
    useStore.setState({
      specialistReports: [
        report({
          symbol: "AAPL",
          recommendation: "BUY",
          groundingOk: true,
          volBandSigmaPct: 4.4,
          investmentThesis: "Specialist leans bullish on AAPL this cycle.",
        }),
        report({ symbol: "TSLA", recommendation: "SELL" }),
      ],
      // Wave 2 (#2732): the row call is the board decision — seed one for AAPL so
      // the authoritative BUY renders (the specialist rec is no longer a headline).
      roundTable: [
        { symbol: "AAPL", action: "BUY", passed: true, conviction: 0.3, sector: "", votesFor: 1, votesAbstain: 0, votesAgainst: 0, vetoReason: "", ts: "14:35", senators: [] },
      ],
    });
    render(<Reports />);
    // The first report auto-expands; the board's BUY shows on the list row, and the
    // thesis shows in the card (Synthesis prose). Assert PRESENCE (>=1).
    expect(screen.getAllByText("BUY").length).toBeGreaterThan(0); // board call
    expect(screen.getAllByText(/leans bullish on AAPL/i).length).toBeGreaterThan(0); // thesis
  });

  it("no longer renders the Round-Table decisions section (moved to the Decisions page)", () => {
    render(<Reports />);
    expect(screen.queryByText(/round table/i)).toBeNull();
    expect(screen.queryByText(/no round-table decisions/i)).toBeNull();
  });

  // RQ-1: a card's per-agent signal (e.g. HOLD) can differ from the executed
  // trade, because orders follow a weighted consensus. Surface that verbatim,
  // directly under the heading, so BUY-despite-HOLD reads as intended, not buggy.
  it("shows the consensus explainer note under the heading (always, even when empty)", () => {
    const { container } = render(<Reports />);
    expect(screen.getByText(/How to read these reports/i)).toBeTruthy();
    // Wave 2 (#2732): the Round-Table decision is the authoritative call; the
    // specialist is one of many assistants whose weighted vote feeds it.
    expect(container.textContent).toMatch(/the board.s collective vote/i);
  });
});
