import type { OpenOrder } from "@/lib/api";

/**
 * Open-orders adapter (#2137): map the engine's /open-orders response (the operator's currently
 * OPEN / working Alpaca orders) to the console shape. Fail-safe like the activities adapter:
 *   - side normalises to "buy" | "sell" (out-of-contract → "buy", never crashes the strip)
 *   - qty uses `?? 0` (a real fractional qty must survive, rendered with fmtQty)
 *   - limit/stop are number | null
 *   - submitted_at parses to a Date, an unparseable/absent stamp collapses to null.
 */
export interface ConsoleOpenOrder {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  type: string;
  limitPrice: number | null;
  stopPrice: number | null;
  status: string;
  submittedAt: Date | null;
}

export function adaptOpenOrders(
  resp: { orders?: OpenOrder[] } | null | undefined,
): ConsoleOpenOrder[] {
  const raw = Array.isArray(resp?.orders) ? resp!.orders! : [];
  return raw.map((o) => {
    const d = o.submitted_at ? new Date(o.submitted_at) : null;
    return {
      id: String(o.id ?? ""),
      symbol: String(o.symbol ?? ""),
      side: o.side === "sell" ? "sell" : "buy",
      qty: o.qty ?? 0,
      type: String(o.type ?? ""),
      limitPrice: typeof o.limit_price === "number" ? o.limit_price : null,
      stopPrice: typeof o.stop_price === "number" ? o.stop_price : null,
      status: String(o.status ?? ""),
      submittedAt: d && !Number.isNaN(d.getTime()) ? d : null,
    };
  });
}
