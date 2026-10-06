import { describe, it, expect } from "vitest";
import { adaptOpenOrders } from "@/console/live/openOrders";

describe("adaptOpenOrders (#2137)", () => {
  it("maps snake_case open orders to the console shape", () => {
    const [o] = adaptOpenOrders({
      status: "success",
      orders: [
        {
          id: "o1",
          symbol: "AAPL",
          side: "buy",
          qty: 0.2057,
          type: "limit",
          limit_price: 320,
          stop_price: null,
          status: "new",
          submitted_at: "2026-07-15T16:39:00Z",
        },
      ],
    });
    expect(o.symbol).toBe("AAPL");
    expect(o.side).toBe("buy");
    expect(o.qty).toBe(0.2057); // fractional precision preserved
    expect(o.type).toBe("limit");
    expect(o.limitPrice).toBe(320);
    expect(o.stopPrice).toBeNull();
    expect(o.status).toBe("new");
    expect(o.submittedAt).toBeInstanceOf(Date);
  });

  it("is fail-safe on null/empty/garbage input", () => {
    expect(adaptOpenOrders(null)).toEqual([]);
    expect(adaptOpenOrders(undefined)).toEqual([]);
    expect(adaptOpenOrders({})).toEqual([]);
    const [o] = adaptOpenOrders({ orders: [{}] });
    expect(o.symbol).toBe("");
    expect(o.side).toBe("buy"); // out-of-contract / missing → "buy"
    expect(o.qty).toBe(0);
    expect(o.limitPrice).toBeNull();
    expect(o.submittedAt).toBeNull();
  });

  it("normalises an out-of-contract side + an unparseable timestamp", () => {
    const [o] = adaptOpenOrders({ orders: [{ side: "weird", submitted_at: "not-a-date" }] });
    expect(o.side).toBe("buy");
    expect(o.submittedAt).toBeNull();
  });
});
