// #2822: after a Paper↔Live switch the Overview still showed the OLD account's order/HITL/
// decision data. The mode-switch freshness machinery (#2465 gate + "Bug B" account affinity)
// covers the portfolio snapshot and the equity curves — but openOrders / activities /
// pendingApprovals / roundTable were neither cleared at switch start, nor write-guarded while
// the switch is in flight, so the paper values survived the reveal and an in-flight poll
// against the DYING old engine could even repaint them afterwards. Three layers under test:
//   1. store.resetAccountFeeds() clears the four account-scoped feed slices;
//   2. the four pollers DROP writes while modeSwitch != null (mirroring useEquityPolling);
//   3. LiveConsentModal calls resetAccountFeeds() on BOTH switch paths (source tripwire).
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useStore } from "@/console/store/useStore";

vi.mock("@/lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({
    status: "success",
    orders: [{ id: "o1", symbol: "AAPL", side: "buy", qty: 1, type: "limit", limit_price: 100, status: "new" }],
  }),
  fetchActivities: vi.fn().mockResolvedValue({
    status: "success",
    trades: [{ id: "t1", symbol: "AAPL", side: "buy", qty: 1, price: 100 }],
    truncated: false,
  }),
  fetchHitlPending: vi.fn().mockResolvedValue({
    items: [{ approval_id: "ap1", symbol: "AAPL", action: "BUY", qty: 1, price: 100 }],
  }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue({
    decisions: [{ id: "d1", symbol: "AAPL", action: "BUY" }],
  }),
}));

import { useOpenOrdersPolling } from "@/console/live/useOpenOrdersPolling";
import { useActivitiesPolling } from "@/console/live/useActivitiesPolling";
import { useHitlPendingPolling } from "@/console/live/useHitlPendingPolling";
import { useRoundTablePolling } from "@/console/live/useRoundTablePolling";

const SEEDED = {
  openOrders: [{ id: "old", symbol: "PAPER", side: "buy", qty: 1, type: "limit", limitPrice: 1, stopPrice: null, status: "new", submittedAt: null }],
  activities: [{ id: "old", symbol: "PAPER", side: "buy", qty: 1, price: 1, filledAt: null }],
  activitiesTruncated: true,
  pendingApprovals: [{ approvalId: "old", symbol: "PAPER", side: "buy", qty: 1, price: 1, createdAt: null }],
  roundTable: [{ id: "old" }],
} as never;

describe("#2822 layer 1: resetAccountFeeds clears every account-scoped feed slice", () => {
  it("empties openOrders, activities(+truncated), pendingApprovals and roundTable", () => {
    useStore.setState(SEEDED);
    useStore.getState().resetAccountFeeds();
    const s = useStore.getState();
    expect(s.openOrders).toEqual([]);
    expect(s.activities).toEqual([]);
    expect(s.activitiesTruncated).toBe(false);
    expect(s.pendingApprovals).toEqual([]);
    expect(s.roundTable).toEqual([]);
  });
});

describe("#2822 layer 2: pollers drop writes while a mode switch is in flight", () => {
  beforeEach(() => {
    useStore.setState({
      openOrders: [],
      activities: [],
      activitiesTruncated: false,
      pendingApprovals: [],
      roundTable: [],
      modeSwitch: { to: "live", startedAt: Date.now() } as never,
    });
  });

  it("useOpenOrdersPolling: no write during the switch; writes again after it clears", async () => {
    const a = renderHook(() => useOpenOrdersPolling());
    await new Promise((r) => setTimeout(r, 30));
    expect(useStore.getState().openOrders).toEqual([]); // stale old-engine data dropped
    a.unmount();
    useStore.getState().setModeSwitch(null);
    const b = renderHook(() => useOpenOrdersPolling());
    await waitFor(() => expect(useStore.getState().openOrders.length).toBe(1));
    b.unmount();
  });

  it("useActivitiesPolling: no write during the switch", async () => {
    const h = renderHook(() => useActivitiesPolling());
    await new Promise((r) => setTimeout(r, 30));
    expect(useStore.getState().activities).toEqual([]);
    h.unmount();
  });

  it("useHitlPendingPolling: no write during the switch", async () => {
    const h = renderHook(() => useHitlPendingPolling());
    await new Promise((r) => setTimeout(r, 30));
    expect(useStore.getState().pendingApprovals).toEqual([]);
    h.unmount();
  });

  it("useRoundTablePolling: no write during the switch", async () => {
    const h = renderHook(() => useRoundTablePolling());
    await new Promise((r) => setTimeout(r, 30));
    expect(useStore.getState().roundTable).toEqual([]);
    h.unmount();
  });
});

describe("#2822 layer 3: LiveConsentModal clears the feeds on BOTH switch paths", () => {
  it("source tripwire: resetAccountFeeds() sits next to the existing resets in toLive AND toPaper", () => {
    const src = readFileSync(resolve(process.cwd(), "src/console/desktop/LiveConsentModal.tsx"), "utf8");
    const calls = src.match(/resetAccountFeeds\(\)/g) ?? [];
    expect(calls.length, "expected a resetAccountFeeds() call in toLive AND in toPaper").toBeGreaterThanOrEqual(2);
    expect(src).toContain("resetAccountSnapshot"); // the existing resets stay
  });
});
