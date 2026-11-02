import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Positions } from "../console/desktop/pages/Positions";
import { useStore } from "../console/store/useStore";

// Positions polls /portfolio-summary on mount; stub the fetch so the test is
// deterministic and the page renders from seeded store state.
vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
}));

describe("console Positions page", () => {
  beforeEach(() => {
    useStore.setState({ positions: [], cashEUR: null, currentEquity: null });
  });

  it("shows an empty/warming state with no positions", () => {
    render(<Positions />);
    expect(screen.getByText(/no open positions/i)).toBeTruthy();
    expect(screen.getByText(/0 Active Positions/i)).toBeTruthy();
  });

  it("renders a row and the derived totals from the store", () => {
    useStore.setState({
      currentEquity: 100_000,
      cashEUR: 98_000,
      positions: [
        {
          symbol: "AAPL",
          name: "AAPL",
          qty: 10,
          avgEntry: 180,
          last: 200,
          marketValue: 2000,
          unrealizedEUR: 200,
          unrealizedPct: 11.1,
          weight: 2,
          heldDays: 5,
        },
      ],
    });
    render(<Positions />);
    expect(screen.getByText("1 Active Positions")).toBeTruthy();
    // symbol is "AAPL"; the Name column shows an honest "—" (no company name)
    expect(screen.getAllByText("AAPL").length).toBeGreaterThanOrEqual(1);
    // cash formatted de-DE → €98.000,00
    expect(screen.getByText(/98,000\.00/)).toBeTruthy();
  });

  it("drops the Trend column entirely — no '30d'/'Trend' header and no row sparkline", () => {
    // The engine serves no per-symbol price history, so a real 20-day trend cannot be
    // drawn; a 2-point entry→last segment was not a trend and is removed.
    useStore.setState({
      currentEquity: 100_000,
      cashEUR: 98_000,
      positions: [
        {
          symbol: "AAPL",
          name: "AAPL",
          qty: 10,
          avgEntry: 180,
          last: 200,
          marketValue: 2000,
          unrealizedEUR: 200,
          unrealizedPct: 11.1,
          weight: 2,
          heldDays: 5,
          changeTodayPct: null,
        },
      ],
    });
    const { container } = render(<Positions />);
    expect(screen.queryByText("30d")).toBeNull();
    expect(screen.queryByText("Trend")).toBeNull();
    // no sparkline SVG in any row anymore
    expect(container.querySelector("tbody svg")).toBeNull();
    // name === symbol → honest "—" in the Name column, never a redundant symbol.
    expect(screen.getAllByText("AAPL").length).toBe(1);
  });

  it("Last column shows the price with today's move beneath it: currency, then percent in parens", () => {
    useStore.setState({
      currentEquity: 100_000,
      cashEUR: 98_000,
      positions: [
        {
          symbol: "AAPL",
          name: "AAPL",
          qty: 10,
          avgEntry: 180,
          last: 200,
          marketValue: 2000,
          unrealizedEUR: 200,
          unrealizedPct: 11.1,
          weight: 2,
          heldDays: 5,
          changeTodayPct: 2.0, // +2% today → +$39.22 on a $2,000 mark
        },
      ],
    });
    const { container } = render(<Positions />);
    // today's move renders as one non-wrapping line "…(+2.00%)" — percent in parentheses.
    expect(screen.getByText(/\(\+2\.00%\)/)).toBeTruthy();
    // and it carries the tabular-nums, no-wrap treatment (design: never breaks mid-value).
    const moveLine = screen.getByText(/\(\+2\.00%\)/).closest("div");
    expect(moveLine?.className).toMatch(/tabular-nums/);
    const lastCell = container.querySelector("tbody tr td:nth-child(5)");
    expect(lastCell?.className).toMatch(/whitespace-nowrap/);
  });
});
