import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Overview } from "../console/desktop/pages/Overview";
import { useStore } from "../console/store/useStore";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";

// Overview mounts three polls (portfolio, equity, round-table); stub the fetches
// so the test renders from seeded store state deterministically.
vi.mock("../lib/api", () => ({
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue(null),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
}));

const day = (n: number) => new Date(2026, 5, n, 16);

const decision = (over: Partial<ConsoleRoundTableDecision> = {}): ConsoleRoundTableDecision => ({
  symbol: "AAPL",
  action: "BUY",
  passed: true,
  conviction: 0.62,
  sector: "Tech",
  votesFor: 5,
  votesAbstain: 2,
  votesAgainst: 1,
  vetoReason: "",
  ts: day(12).toISOString(),
  senators: [],
  ...over,
});

describe("console Overview page", () => {
  beforeEach(() => {
    useStore.setState({
      positions: [],
      cashEUR: null,
      currentEquity: null,
      lastEquity: null,
      equityCurve: [],
      benchmarkCurve: [],
      roundTable: [],
    });
  });

  it("renders the greeting + empty hero with no data", () => {
    render(<Overview />);
    expect(screen.getByText(/good (morning|afternoon|evening)/i)).toBeTruthy();
    // equity hero shows the em-dash placeholder before any data
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the full dashboard chrome (market pill, KPI cards, two-up sections)", () => {
    render(<Overview />);
    // Hero pill + the since-inception summary row ("Since inception" also appears
    // as the Max-drawdown card hint, so it legitimately matches more than once)
    expect(screen.getByText(/Market/i)).toBeTruthy();
    expect(screen.getAllByText(/Since inception/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/S&P 500 today/i)).toBeTruthy();
    expect(screen.getByText(/Started/i)).toBeTruthy();
    // All four KPI cards
    expect(screen.getByText(/Daily P \/ L/i)).toBeTruthy();
    expect(screen.getByText(/Max drawdown/i)).toBeTruthy();
    expect(screen.getByText(/Sharpe/i)).toBeTruthy();
    expect(screen.getByText(/Cash \/ margin/i)).toBeTruthy();
    // Two-up Decision-queue + Top-positions sections (with empty states)
    expect(screen.getByText(/Decision queue/i)).toBeTruthy();
    expect(screen.getByText(/Top positions/i)).toBeTruthy();
    expect(screen.getByText(/No decisions yet/i)).toBeTruthy();
    expect(screen.getByText(/No open positions/i)).toBeTruthy();
  });

  it("renders equity, daily P/L and the range toggles from seeded data", () => {
    useStore.setState({
      currentEquity: 102_000,
      lastEquity: 101_000,
      cashEUR: 50_000,
      positions: [
        { symbol: "AAPL", name: "AAPL", qty: 1, avgEntry: 1, last: 1, marketValue: 1, unrealizedEUR: 0, unrealizedPct: 0, weight: 0, heldDays: 1 },
      ],
      equityCurve: [
        { t: day(10), eur: 100_000 },
        { t: day(11), eur: 101_000 },
        { t: day(12), eur: 102_000 },
      ],
    });
    render(<Overview />);
    expect(screen.getByText(/102\.000,00/)).toBeTruthy(); // current equity
    expect(screen.getAllByText(/today/i).length).toBeGreaterThanOrEqual(1); // daily P/L line
    expect(screen.getByRole("button", { name: "1M" })).toBeTruthy(); // range toggle
    expect(screen.getByText(/€50\.00k/)).toBeTruthy(); // cash card (compact, original-faithful)
  });

  it("renders a round-table decision row in the decision queue", () => {
    useStore.setState({ roundTable: [decision({ symbol: "NVDA", action: "BUY" })] });
    render(<Overview />);
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("BUY")).toBeTruthy();
    expect(screen.getByText(/1 decision this session/i)).toBeTruthy();
  });
});
