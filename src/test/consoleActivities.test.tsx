import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Activities } from "../console/desktop/pages/Activities";
import { useStore } from "../console/store/useStore";
import { navItems, validPages } from "../console/desktop/nav";
import type { ConsoleOpenOrder } from "../console/live/openOrders";

// The Orders page (#2137) polls /activities, /open-orders AND /api/hitl/pending (#2660) on
// mount; stub all three + the action calls.
vi.mock("../lib/api", () => ({
  fetchActivities: vi.fn().mockResolvedValue({ status: "success", trades: [], truncated: false }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchHitlPending: vi.fn().mockResolvedValue({ items: [] }),
  approveHitlOrder: vi.fn().mockResolvedValue({ success: true, approval_id: "", detail: "" }),
  rejectHitlOrder: vi.fn().mockResolvedValue({ success: true, approval_id: "", detail: "" }),
}));

// No-op the polling hooks so the store is driven purely by the seeded state + the Cancel action
// under test — otherwise a poll resolving mid-test races the cancel and rewrites openOrders.
vi.mock("../console/live/useOpenOrdersPolling", () => ({ useOpenOrdersPolling: () => {} }));
vi.mock("../console/live/useActivitiesPolling", () => ({ useActivitiesPolling: () => {} }));
vi.mock("../console/live/useHitlPendingPolling", () => ({ useHitlPendingPolling: () => {} }));

const OPEN: ConsoleOpenOrder = {
  id: "o1",
  symbol: "AAPL",
  side: "buy",
  qty: 0.2057,
  type: "limit",
  limitPrice: 320,
  stopPrice: null,
  status: "new",
  submittedAt: new Date("2026-07-15T16:39:00Z"),
};

describe("console Orders page (#2137)", () => {
  beforeEach(() => {
    useStore.setState({ activities: [], activitiesTruncated: false, openOrders: [] });
  });

  it("has both section headings (Order book + Order history) and empty states", () => {
    render(<Activities />);
    expect(screen.getByText("Order book")).toBeTruthy();
    expect(screen.getByText("Order history")).toBeTruthy();
    expect(screen.getByText(/no open orders/i)).toBeTruthy();
    expect(screen.getByText(/no filled trades yet/i)).toBeTruthy();
  });

  it("lists a working order in the Order book (fractional qty + type + limit)", () => {
    useStore.setState({ openOrders: [OPEN] });
    render(<Activities />);
    expect(screen.getByText(/1 working order\b/i)).toBeTruthy();
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("0.2057")).toBeTruthy(); // #2587: en-US decimal point
    expect(screen.getByText("limit")).toBeTruthy();
    expect(screen.getByText("$320.00")).toBeTruthy();
  });

  it("HITL: every open order has a 2-step Cancel control that cancels + removes it", async () => {
    useStore.setState({ openOrders: [OPEN] });
    const cancelOrder = vi.fn(async (_id: string) => ({ status: "success" }));
    render(<Activities cancelOrder={cancelOrder} />);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i })); // arm
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i })); // confirm
    await waitFor(() => expect(cancelOrder).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(useStore.getState().openOrders).toHaveLength(0));
  });

  it("keeps the order + surfaces an error when the cancel FAILS (no false optimistic removal)", async () => {
    useStore.setState({ openOrders: [OPEN] });
    const cancelOrder = vi.fn(async (_id: string) => ({ status: "error" }));
    render(<Activities cancelOrder={cancelOrder} />);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() => expect(cancelOrder).toHaveBeenCalledWith("o1"));
    // key behaviour: the order is NOT falsely removed when the cancel fails
    expect(useStore.getState().openOrders).toHaveLength(1);
    // and the failure is surfaced to the operator
    await waitFor(() => expect(screen.getByText(/cancel failed/i)).toBeTruthy(), { timeout: 2000 });
  });

  it("keeps the order + surfaces an error when the cancel call THROWS (no unhandled rejection)", async () => {
    useStore.setState({ openOrders: [OPEN] });
    const cancelOrder = vi.fn(async (_id: string): Promise<{ status: string }> => {
      throw new Error("network down");
    });
    render(<Activities cancelOrder={cancelOrder} />);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() => expect(cancelOrder).toHaveBeenCalledWith("o1"));
    // a thrown rejection is swallowed → order kept, failure surfaced (not an optimistic removal)
    expect(useStore.getState().openOrders).toHaveLength(1);
    await waitFor(() => expect(screen.getByText(/cancel failed/i)).toBeTruthy(), { timeout: 2000 });
  });

  it("renders a fill row with the derived value + fractional qty", () => {
    useStore.setState({
      activities: [
        { id: "f1", symbol: "AAPL", side: "buy", qty: 0.2057, price: 324.02, filledAt: new Date("2026-07-15T14:39:00Z") },
      ],
    });
    render(<Activities />);
    expect(screen.getByText("0.2057")).toBeTruthy(); // #2587: en-US decimal point
    expect(screen.getByText(/66\.65/)).toBeTruthy();
  });

  it("registers 'activities' as a nav item labelled 'Orders' + a valid deep-link page", () => {
    const item = navItems(false).find((i) => i.id === "activities");
    expect(item?.label).toBe("Orders");
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
