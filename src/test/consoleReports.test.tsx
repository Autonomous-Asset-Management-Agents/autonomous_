import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Reports } from "../console/desktop/pages/Reports";
import { useStore } from "../console/store/useStore";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";
import type { SpecialistReport } from "../console/types";

// The page mounts two polls (round-table + specialist); stub both fetches so the
// test renders from seeded store state deterministically.
vi.mock("../lib/api", () => ({
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
}));

const decision = (over: Partial<ConsoleRoundTableDecision> = {}): ConsoleRoundTableDecision => ({
  symbol: "AIG",
  action: "HOLD",
  passed: false,
  conviction: 0.1,
  sector: "Financials",
  votesFor: 3,
  votesAbstain: 5,
  votesAgainst: 1,
  vetoReason: "",
  ts: "17:12",
  senators: [
    { name: "Alpha", vote: "BULL", conviction: 0.5, reasoning: "upside", hardVeto: false },
    { name: "Delta", vote: "BEAR", conviction: 0.9, reasoning: "risk", hardVeto: true },
  ],
  ...over,
});

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

describe("console Reports page", () => {
  beforeEach(() => {
    useStore.setState({
      roundTable: [],
      specialistReports: [],
      specialistStatus: "",
      specialistMessage: null,
    });
  });

  it("shows the empty states for both Round-Table and specialist sections", () => {
    render(<Reports />);
    expect(screen.getByText(/no round-table decisions yet/i)).toBeTruthy();
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

  it("renders a Round-Table decision with its vote tally + vetoing senator", () => {
    useStore.setState({ roundTable: [decision()] });
    render(<Reports />);
    expect(screen.getByText("1 decision")).toBeTruthy();
    expect(screen.getByText("AIG")).toBeTruthy();
    expect(screen.getByText("Delta")).toBeTruthy();
    expect(screen.getByText("VETO")).toBeTruthy(); // hard-veto badge
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
    // the deterministic decision summary renders too
    expect(screen.getByText(/leans bullish on AAPL/i)).toBeTruthy();
  });
});
