import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Activities } from "../console/desktop/pages/Activities";
import { useStore } from "../console/store/useStore";
import { adaptHitlPending, type ConsolePendingApproval } from "../console/live/hitlQueue";
import { approvePendingOrder, rejectPendingOrder, ApproveButton } from "../console/desktop/orderApprove";

/**
 * #2660 (Epic #2667): desktop integration of the engine's per-order HITL approval queue.
 * The backend (HitlQueue + /api/hitl/pending|approve|reject + audited drain) already shipped
 * with the PR-0a-ii series — these tests cover the CONSOLE half only: the fail-safe adapter,
 * the optimistic approve/reject helpers, the two-step ApproveButton, and the "Awaiting
 * approval" section on the Orders page.
 */

// The Orders page polls /activities + /open-orders + /api/hitl/pending on mount; stub the api
// module and no-op the polling hooks so the store is driven purely by seeded state.
vi.mock("../lib/api", () => ({
  fetchActivities: vi.fn().mockResolvedValue({ status: "success", trades: [], truncated: false }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchHitlPending: vi.fn().mockResolvedValue({ items: [] }),
  approveHitlOrder: vi.fn().mockResolvedValue({ success: true, approval_id: "", detail: "" }),
  rejectHitlOrder: vi.fn().mockResolvedValue({ success: true, approval_id: "", detail: "" }),
}));
vi.mock("../console/live/useOpenOrdersPolling", () => ({ useOpenOrdersPolling: () => {} }));
vi.mock("../console/live/useActivitiesPolling", () => ({ useActivitiesPolling: () => {} }));
vi.mock("../console/live/useHitlPendingPolling", () => ({ useHitlPendingPolling: () => {} }));

const PENDING: ConsolePendingApproval = {
  approvalId: "ap-1",
  symbol: "NVDA",
  side: "buy",
  qty: 1.5,
  price: 120,
  createdAt: new Date("2026-08-04T14:30:00Z"),
};

describe("hitlQueue adapter (fail-safe mapping of /api/hitl/pending)", () => {
  it("maps a well-formed item and normalises the side", () => {
    const rows = adaptHitlPending({
      items: [
        {
          approval_id: "ap-1",
          user_id: "global",
          symbol: "NVDA",
          action: "BUY",
          qty: 1.5,
          price: 120,
          conviction: 0.8,
          target_weight: 0.05,
          created_at: "2026-08-04T14:30:00Z",
        },
      ],
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].approvalId).toBe("ap-1");
    expect(rows[0].symbol).toBe("NVDA");
    expect(rows[0].side).toBe("buy");
    expect(rows[0].qty).toBe(1.5);
    expect(rows[0].price).toBe(120);
    expect(rows[0].createdAt?.toISOString()).toBe("2026-08-04T14:30:00.000Z");
  });

  it("never crashes on malformed items (missing fields, bad timestamp) and drops nothing", () => {
    const rows = adaptHitlPending({
      items: [
        { approval_id: "ap-2", action: "sell", created_at: "not-a-date" },
        {},
      ] as never,
    });
    expect(rows).toHaveLength(2);
    expect(rows[0].side).toBe("sell");
    expect(rows[0].createdAt).toBeNull();
    expect(rows[1].symbol).toBe("");
    expect(rows[1].qty).toBe(0);
  });

  it("collapses a null/undefined response to an empty list", () => {
    expect(adaptHitlPending(null)).toEqual([]);
    expect(adaptHitlPending(undefined)).toEqual([]);
    expect(adaptHitlPending({} as never)).toEqual([]);
  });
});

describe("approve/reject helpers (optimistic removal, failure never masked)", () => {
  beforeEach(() => {
    useStore.setState({ pendingApprovals: [PENDING] });
  });

  it("approvePendingOrder removes the row on success (next poll reconciles)", async () => {
    const approve = vi.fn(async (_id: string) => ({ success: true, approval_id: "ap-1", detail: "" }));
    const ok = await approvePendingOrder("ap-1", approve);
    expect(ok).toBe(true);
    expect(approve).toHaveBeenCalledWith("ap-1");
    expect(useStore.getState().pendingApprovals).toHaveLength(0);
  });

  it("keeps the row when the engine says success:false (expired / not found)", async () => {
    const approve = vi.fn(async (_id: string) => ({ success: false, approval_id: "ap-1", detail: "not found or expired" }));
    const ok = await approvePendingOrder("ap-1", approve);
    expect(ok).toBe(false);
    expect(useStore.getState().pendingApprovals).toHaveLength(1);
  });

  it("keeps the row when the call REJECTS (no unhandled rejection, no false removal)", async () => {
    const approve = vi.fn(async (_id: string) => {
      throw new Error("engine offline");
    });
    const ok = await approvePendingOrder("ap-1", approve);
    expect(ok).toBe(false);
    expect(useStore.getState().pendingApprovals).toHaveLength(1);
  });

  it("rejectPendingOrder removes the row on success and keeps it on failure", async () => {
    const rejectOk = vi.fn(async (_id: string, _reason?: string) => ({ success: true, approval_id: "ap-1", detail: "rejected" }));
    expect(await rejectPendingOrder("ap-1", rejectOk)).toBe(true);
    expect(useStore.getState().pendingApprovals).toHaveLength(0);

    useStore.setState({ pendingApprovals: [PENDING] });
    const rejectFail = vi.fn(async (_id: string, _reason?: string) => ({ success: false, approval_id: "ap-1", detail: "" }));
    expect(await rejectPendingOrder("ap-1", rejectFail)).toBe(false);
    expect(useStore.getState().pendingApprovals).toHaveLength(1);
  });
});

describe("ApproveButton (two-step arm→confirm — a mis-click must never fire live money)", () => {
  it("arms first, then confirms; onApprove only fires on the confirm click", async () => {
    const onApprove = vi.fn(async () => true);
    render(<ApproveButton onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i })); // arm
    expect(onApprove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i })); // confirm
    await waitFor(() => expect(onApprove).toHaveBeenCalledTimes(1));
  });

  it("shows an error and stays armed for retry when the approve fails", async () => {
    const onApprove = vi.fn(async () => false);
    render(<ApproveButton onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() => expect(screen.getByText(/approve failed/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /^confirm$/i })).toBeTruthy();
  });

  it("can be disarmed without approving (Keep)", () => {
    const onApprove = vi.fn(async () => true);
    render(<ApproveButton onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^keep$/i }));
    expect(onApprove).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^approve$/i })).toBeTruthy();
  });
});

describe("Orders page: Awaiting approval section (#2660)", () => {
  beforeEach(() => {
    useStore.setState({
      activities: [],
      activitiesTruncated: false,
      openOrders: [],
      pendingApprovals: [],
    });
  });

  it("renders NO approval section when the queue is empty", () => {
    render(<Activities />);
    expect(screen.queryByText(/awaiting approval/i)).toBeNull();
  });

  it("renders a pending approval row with symbol, side, qty and an Approve control", () => {
    useStore.setState({ pendingApprovals: [PENDING] });
    render(<Activities />);
    expect(screen.getByText(/awaiting approval/i)).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^approve$/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^reject$/i })).toBeTruthy();
  });

  it("approves via the two-step control and removes the row", async () => {
    useStore.setState({ pendingApprovals: [PENDING] });
    const approveOrder = vi.fn(async (_id: string) => ({ success: true, approval_id: "ap-1", detail: "" }));
    render(<Activities approveOrder={approveOrder} />);
    fireEvent.click(screen.getByRole("button", { name: /^approve$/i })); // arm
    fireEvent.click(screen.getByRole("button", { name: /^confirm$/i })); // confirm
    await waitFor(() => expect(approveOrder).toHaveBeenCalledWith("ap-1"));
    await waitFor(() => expect(useStore.getState().pendingApprovals).toHaveLength(0));
  });

  it("rejects via the two-step control and removes the row", async () => {
    useStore.setState({ pendingApprovals: [PENDING] });
    const rejectOrder = vi.fn(async (_id: string, _reason?: string) => ({ success: true, approval_id: "ap-1", detail: "rejected" }));
    render(<Activities rejectOrder={rejectOrder} />);
    fireEvent.click(screen.getByRole("button", { name: /^reject$/i })); // arm
    fireEvent.click(screen.getByRole("button", { name: /confirm reject/i })); // confirm
    await waitFor(() => expect(rejectOrder).toHaveBeenCalledWith("ap-1", expect.anything()));
    await waitFor(() => expect(useStore.getState().pendingApprovals).toHaveLength(0));
  });
});
