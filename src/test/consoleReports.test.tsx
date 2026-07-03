import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Reports } from "../console/desktop/pages/Reports";
import { useStore } from "../console/store/useStore";
import type { SpecialistReport } from "../console/types";

// Reports polls /specialist-reports only now (Round-Table decisions moved to the
// Decisions page, #1435); stub the fetch so the test renders from seeded state.
vi.mock("../lib/api", () => ({
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
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

  it("renders one specialist card per report (symbol + recommendation visible)", () => {
    useStore.setState({
      specialistReports: [
        report({ symbol: "AAPL", recommendation: "BUY" }),
        report({ symbol: "TSLA", recommendation: "SELL", summary: "Specialist cautious on TSLA." }),
      ],
    });
    render(<Reports />);
    expect(screen.getByText("2 reports")).toBeTruthy();
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("TSLA")).toBeTruthy();
    expect(screen.getByText("BUY")).toBeTruthy();
    expect(screen.getByText("SELL")).toBeTruthy();
    expect(screen.getByText(/leans bullish on AAPL/i)).toBeTruthy();
  });

  it("no longer renders the Round-Table decisions section (moved to the Decisions page)", () => {
    render(<Reports />);
    expect(screen.queryByText(/round table/i)).toBeNull();
    expect(screen.queryByText(/no round-table decisions/i)).toBeNull();
  });
});
