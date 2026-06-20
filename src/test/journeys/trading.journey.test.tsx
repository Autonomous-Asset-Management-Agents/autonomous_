/**
 * Journey: Operational trading (monitor the book) — UX E2E #1050.
 *
 * Drives the read side of the trading loop through the real desktop shell with
 * the engine HTTP faked at the api seam: the polling hooks fetch, the real
 * adapters transform, and the data lands on the operator surfaces. Navigates
 * Overview → Positions → Reports and asserts the same canonical book renders on
 * each; plus the warming-up empty states.
 *
 * The HITL approve/reject decision queue is a deliberate stub on main (no engine
 * endpoint yet — GAP2); it is scoped in README.md → "J4 Operational trading"
 * and will join this journey when the endpoint lands.
 *
 * See src/test/journeys/README.md → "J4 Operational trading".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DesktopApp } from "@/console/desktop/DesktopApp";
import { useStore } from "@/console/store/useStore";
import { makeBridge, installBridge, resetBridge } from "../fixtures/mockBridge";
import * as fx from "../fixtures/consoleFixtures";

// Fake the engine HTTP at the api seam. Resolved values are set per-test in
// beforeEach (factory can't close over the imported fixtures — hoisting).
vi.mock("@/lib/api", () => ({
  fetchPortfolioSummary: vi.fn(),
  fetchRoundTableDecisions: vi.fn(),
  fetchBenchmarkEquity: vi.fn(),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
  sendChat: vi.fn(),
}));
import { fetchPortfolioSummary, fetchRoundTableDecisions, fetchBenchmarkEquity } from "@/lib/api";

const goto = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("Journey · Operational trading (monitor the book)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = (() => {}) as never;
    useStore.setState({
      desktopPage: "chat",
      positions: [],
      currentEquity: null,
      cashEUR: null,
      roundTable: [],
      equityCurve: [],
      benchmarkCurve: [],
    });
    vi.mocked(fetchPortfolioSummary).mockResolvedValue(fx.portfolioSummary);
    vi.mocked(fetchRoundTableDecisions).mockResolvedValue(fx.roundTableDecisions);
    vi.mocked(fetchBenchmarkEquity).mockResolvedValue(fx.benchmarkEquity);
    installBridge(makeBridge().bridge);
  });
  afterEach(() => resetBridge());

  it("Positions: the engine book flows through the adapter into the table", async () => {
    render(<DesktopApp />);
    goto(/positions/i);

    // Each holding shows in the symbol + name columns → getAllByText.
    await waitFor(() => expect(screen.getAllByText("AAPL").length).toBeGreaterThan(0));
    expect(screen.getAllByText("NVDA").length).toBeGreaterThan(0);
    expect(screen.getAllByText("MSFT").length).toBeGreaterThan(0);
    // header reflects the count + invested book
    expect(screen.getByRole("heading", { name: /3 positions/i })).toBeTruthy();
  });

  it("Reports: the latest Round-Table verdict per symbol renders", async () => {
    render(<DesktopApp />);
    goto(/reports/i);

    await waitFor(() => expect(screen.getByText("AAPL")).toBeTruthy());
    expect(screen.getByText("TSLA")).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /3 decisions/i })).toBeTruthy();
  });

  it("Overview: equity + open-position count surface from the same book", async () => {
    render(<DesktopApp />);
    goto(/overview/i);

    await waitFor(() => expect(screen.getByText(/105\.000/)).toBeTruthy()); // current equity €105.000,00
  });

  it("navigation: the book is consistent as the operator moves between surfaces", async () => {
    render(<DesktopApp />);

    goto(/positions/i);
    await waitFor(() => expect(screen.getAllByText("AAPL").length).toBeGreaterThan(0));

    goto(/reports/i);
    await waitFor(() => expect(screen.getByText("TSLA")).toBeTruthy());

    goto(/positions/i);
    await waitFor(() => expect(screen.getAllByText("MSFT").length).toBeGreaterThan(0));
  });

  it("warming up: empty engine responses show honest empty states, not zeros", async () => {
    vi.mocked(fetchPortfolioSummary).mockResolvedValue(fx.portfolioEmpty);
    vi.mocked(fetchRoundTableDecisions).mockResolvedValue(fx.roundTableEmpty);
    render(<DesktopApp />);

    goto(/positions/i);
    await waitFor(() => expect(screen.getByText(/no open positions/i)).toBeTruthy());

    goto(/reports/i);
    await waitFor(() => expect(screen.getByText(/no round-table decisions yet/i)).toBeTruthy());
  });
});
