import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Positions } from "../console/desktop/pages/Positions";
import { useStore } from "../console/store/useStore";

// Positions polls /portfolio-summary on mount; stub the fetch so the test is
// deterministic and the page renders from seeded store state.
vi.mock("../lib/api", () => ({ fetchPortfolioSummary: vi.fn().mockResolvedValue(null) }));

describe("console Positions page", () => {
  beforeEach(() => {
    useStore.setState({ positions: [], cashEUR: null, currentEquity: null });
  });

  it("shows an empty/warming state with no positions", () => {
    render(<Positions />);
    expect(screen.getByText(/no open positions/i)).toBeTruthy();
    expect(screen.getByText(/0 positions/i)).toBeTruthy();
  });

  it("renders a row and the derived totals from the store", () => {
    useStore.setState({
      currentEquity: 100_000,
      cashEUR: 98_000,
      positions: [
        {
          symbol: "AAPL", name: "AAPL", qty: 10, avgEntry: 180, last: 200,
          marketValue: 2000, unrealizedEUR: 200, unrealizedPct: 11.1, weight: 2, heldDays: 5,
        },
      ],
    });
    render(<Positions />);
    expect(screen.getByText("1 positions")).toBeTruthy();
    // symbol is "AAPL"; the Name column shows an honest "—" (no company name)
    expect(screen.getAllByText("AAPL").length).toBeGreaterThanOrEqual(1);
    // cash formatted de-DE → €98.000,00
    expect(screen.getByText(/98\.000,00/)).toBeTruthy();
  });

  it("renders an honest 2-point trend sparkline (no synthetic Math.sin) and no '30d' header", () => {
    useStore.setState({
      currentEquity: 100_000,
      cashEUR: 98_000,
      positions: [
        {
          symbol: "AAPL", name: "AAPL", qty: 10, avgEntry: 180, last: 200,
          marketValue: 2000, unrealizedEUR: 200, unrealizedPct: 11.1, weight: 2, heldDays: 5,
        },
      ],
    });
    const { container } = render(<Positions />);
    // Honest sparkline = a real avgEntry→last segment: exactly 2 points (one M + one L).
    // The old synthetic Math.sin curve produced 24 points.
    const path = container.querySelector("svg path");
    expect(path).toBeTruthy();
    const cmds = (path!.getAttribute("d") || "").match(/[ML]/g) || [];
    expect(cmds.length).toBe(2);
    // The misleading "30d" header is gone, replaced by "Trend".
    expect(screen.queryByText("30d")).toBeNull();
    expect(screen.getByText("Trend")).toBeTruthy();
    // name === symbol → honest "—" in the Name column, never a redundant symbol.
    expect(screen.getAllByText("AAPL").length).toBe(1);
  });
});
