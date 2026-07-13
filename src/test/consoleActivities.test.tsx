import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Activities } from "../console/desktop/pages/Activities";
import { useStore } from "../console/store/useStore";
import { navItems, validPages } from "../console/desktop/nav";

// Activities polls /activities on mount; stub the fetch so the test is
// deterministic and the page renders from seeded store state.
vi.mock("../lib/api", () => ({
  fetchActivities: vi.fn().mockResolvedValue({ status: "success", trades: [], truncated: false }),
}));

describe("console Activities page", () => {
  beforeEach(() => {
    useStore.setState({ activities: [], activitiesTruncated: false });
  });

  it("shows an empty/warming state with no trades", () => {
    render(<Activities />);
    expect(screen.getByText(/no filled trades yet/i)).toBeTruthy();
    expect(screen.getByText(/0 Executed Trades/i)).toBeTruthy();
  });

  it("renders a fill row with side, qty, price and derived value", () => {
    useStore.setState({
      activities: [
        { id: "f1", symbol: "AMZN", side: "buy", qty: 10, price: 150, filledAt: new Date("2026-02-20T15:00:00Z") },
      ],
    });
    render(<Activities />);
    expect(screen.getByText("1 Executed Trades")).toBeTruthy();
    expect(screen.getByText("AMZN")).toBeTruthy();
    expect(screen.getByText("buy")).toBeTruthy();
    // value = 10 * 150 = 1500 → de-DE €1.500,00
    expect(screen.getByText(/1\.500,00/)).toBeTruthy();
  });

  it("registers 'activities' as a nav item and a valid deep-link page", () => {
    expect(navItems(false).some((i) => i.id === "activities")).toBe(true);
    expect(validPages(false).has("activities")).toBe(true);
  });

  it("shows a truncation note when the history hit the page cap", () => {
    useStore.setState({
      activities: [{ id: "f1", symbol: "AMZN", side: "buy", qty: 1, price: 1, filledAt: null }],
      activitiesTruncated: true,
    });
    render(<Activities />);
    expect(screen.getByText(/history truncated at the page cap/i)).toBeTruthy();
  });
});
