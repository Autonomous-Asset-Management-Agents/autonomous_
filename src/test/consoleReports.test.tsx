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
    useStore.setState({ specialistReports: [], specialistStatus: "", specialistMessage: null });
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
    });
    render(<Reports />);
    // Card-only reader (2026-07-23): ONE grounded card for the selected symbol.
    expect(screen.getByText("BUY")).toBeTruthy(); // recommendation pill on the card
    expect(screen.getByText(/leans bullish on AAPL/i)).toBeTruthy(); // thesis prose
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
    expect(container.textContent).toMatch(/collective vote of all its AI assistants/i);
  });
});
