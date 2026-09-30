import { describe, it, expect } from "vitest";
import { adaptActivities } from "../console/live/activities";
import type { RecentTradesResponse } from "../lib/api";

/**
 * DESK-1: the engine's /recent-trades shape (snake_case, nullable filled_at)
 * must map to the console's Activity shape (camelCase, parsed Date). The
 * normalisation (side, real-0 price, unparseable timestamp) is TDD-pinned.
 */
const resp = (over: Partial<RecentTradesResponse> = {}): RecentTradesResponse => ({
  status: "success",
  trades: [
    { id: "f1", symbol: "AMZN", side: "buy", qty: 10, price: 150, filled_at: "2026-02-20T15:00:00Z" },
  ],
  ...over,
});

describe("adaptActivities", () => {
  it("maps a filled trade to the console Activity shape", () => {
    const a = adaptActivities(resp())[0];
    expect(a.id).toBe("f1");
    expect(a.symbol).toBe("AMZN");
    expect(a.side).toBe("buy");
    expect(a.qty).toBe(10);
    expect(a.price).toBe(150);
    expect(a.filledAt?.toISOString()).toBe("2026-02-20T15:00:00.000Z");
  });

  it("keeps 'sell' and collapses an out-of-contract side to 'buy'", () => {
    expect(
      adaptActivities(resp({ trades: [{ id: "1", symbol: "X", side: "sell", qty: 1, price: 1, filled_at: null }] }))[0].side,
    ).toBe("sell");
    expect(
      adaptActivities({
        status: "ok",
        trades: [{ id: "2", symbol: "Y", side: "short" as unknown as "buy", qty: 1, price: 1, filled_at: null }],
      })[0].side,
    ).toBe("buy");
  });

  it("preserves a real 0 price/qty and a null timestamp", () => {
    const a = adaptActivities(resp({ trades: [{ id: "z", symbol: "Z", side: "buy", qty: 0, price: 0, filled_at: null }] }))[0];
    expect(a.qty).toBe(0);
    expect(a.price).toBe(0);
    expect(a.filledAt).toBeNull();
  });

  it("collapses an unparseable timestamp to null (no Invalid Date)", () => {
    const a = adaptActivities(resp({ trades: [{ id: "b", symbol: "B", side: "buy", qty: 1, price: 1, filled_at: "not-a-date" }] }))[0];
    expect(a.filledAt).toBeNull();
  });

  it("handles an empty / error / null response without throwing", () => {
    expect(adaptActivities({ status: "error", trades: [] })).toEqual([]);
    expect(adaptActivities(null)).toEqual([]);
    expect(adaptActivities(undefined)).toEqual([]);
  });
});
